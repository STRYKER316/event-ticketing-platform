import uuid

import stripe
import structlog
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChargeRequest, PaymentResponse
from app.db.models import Payment, PaymentStatus
from app.db.payment_repository import PaymentRepository
from app.kafka.producers import NotificationProducer, PaymentOutcomeProducer

logger = structlog.get_logger()

_WEBHOOK_OUTCOME_BY_EVENT_TYPE = {
    "payment_intent.succeeded": PaymentStatus.SUCCEEDED,
    "payment_intent.payment_failed": PaymentStatus.FAILED,
}


class PaymentManager:
    def __init__(self, session: AsyncSession, payments: PaymentRepository):
        self._session = session
        self._payments = payments

    async def create_charge(self, payload: ChargeRequest) -> PaymentResponse:
        payment = await self._resolve_payment_row(payload)
        if payment.stripe_charge_id is None:
            # A row with no stripe_charge_id yet means either this is a
            # brand-new attempt, or a previous one never actually reached
            # Stripe (see _submit_to_stripe's error path) — either way this
            # genuinely (re)submits, rather than getting stuck replaying a
            # charge Stripe never received. A row that already has one is
            # skipped entirely: idempotent by construction (§9), Stripe
            # already accepted this idempotency_key, no second API call.
            await self._submit_to_stripe(payment, payload)
            await self._session.commit()
        else:
            logger.info("charge_idempotent_replay", booking_id=str(payload.booking_id))
        return self._build_response(payment)

    async def _resolve_payment_row(self, payload: ChargeRequest) -> Payment:
        existing = await self._payments.get_by_booking_id(payload.booking_id)
        if existing is not None:
            return existing
        payment = Payment(
            booking_id=payload.booking_id,
            ticket_id=payload.ticket_id,
            amount_cents=payload.amount_cents,
            currency=payload.currency,
            idempotency_key=str(payload.booking_id),
        )
        try:
            await self._payments.create(payment)
            await self._session.commit()
            return payment
        except IntegrityError:
            # Lost a race to a concurrent first-time charge attempt for the
            # same booking — the unique index on booking_id caught it
            # (found in code review: this was previously unhandled, a 500
            # instead of a clean idempotent resolution). Use the winner's
            # row instead of erroring out; create_charge's caller-side check
            # on stripe_charge_id decides whether it still needs submitting.
            await self._session.rollback()
            winner = await self._payments.get_by_booking_id(payload.booking_id)
            assert winner is not None, "IntegrityError on booking_id implies a row now exists"
            return winner

    async def _submit_to_stripe(self, payment: Payment, payload: ChargeRequest) -> None:
        # This call only *initiates* the charge attempt (§9 amendment) —
        # confirmation of success/failure is webhook-driven (see
        # payments.py's webhook route), even though Stripe test mode often
        # resolves a PaymentIntent synchronously. Treating the webhook as the
        # sole source of truth is what makes a lost synchronous response (a
        # network blip after Stripe already processed the charge) safe.
        try:
            intent = await stripe.PaymentIntent.create_async(
                amount=payment.amount_cents,
                currency=payment.currency,
                payment_method=payload.payment_method,
                confirm=True,
                idempotency_key=payment.idempotency_key,
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            )
        except stripe.error.StripeError as exc:
            # Warning, not error — same log-level-discipline reasoning as
            # _submit_refund_to_stripe's identical exception type below: a
            # decline or Stripe-side failure is expected/handled, not a
            # system incident (found in P9.T2's cross-service audit — this
            # call site previously logged the same exception class at error).
            logger.warning("stripe_charge_submission_failed", booking_id=str(payment.booking_id), error=str(exc))
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "payment provider unreachable") from exc
        payment.stripe_charge_id = intent.id

    def _build_response(self, payment: Payment) -> PaymentResponse:
        return PaymentResponse.model_validate(payment)

    async def handle_webhook_event(
        self, event: stripe.Event, producer: PaymentOutcomeProducer, notification_producer: NotificationProducer
    ) -> None:
        """Webhook-driven confirmation (§9) — this is the sole source of
        truth for a Payment's terminal status, not create_charge()'s
        synchronous Stripe response. Idempotent by construction (§7's
        general rule, applied to a webhook the same as a Kafka consumer):
        a rowcount-gated conditional UPDATE only transitions a Payment
        that's still PENDING, so a redelivered webhook — or two overlapping
        deliveries racing each other — can't both win it.

        Publishes *before* committing (found in code review): the reverse
        order let a Kafka-publish failure strand a Payment in its new
        terminal status with no way to retry the publish — Stripe's own
        webhook retry would hit the PENDING guard and silently no-op,
        losing the outcome for good. Publishing first means a failure here
        propagates uncommitted (the request handler's session rolls back on
        exception), Stripe sees a non-2xx and genuinely retries, and the
        next attempt finds the row still PENDING and tries again."""
        new_status = _WEBHOOK_OUTCOME_BY_EVENT_TYPE.get(event["type"])
        if new_status is None:
            return
        intent_id = event["data"]["object"]["id"]
        payment = await self._payments.get_by_stripe_charge_id(intent_id)
        if payment is None:
            logger.warning("webhook_payment_not_found", stripe_charge_id=intent_id)
            return
        transitioned = await self._payments.transition_if_pending(intent_id, new_status)
        if not transitioned:
            logger.info(
                "webhook_replay_no_op", booking_id=str(payment.booking_id), current_status=payment.status.value
            )
            return
        payment.status = new_status
        await producer.publish_outcome(payment)
        if new_status is PaymentStatus.SUCCEEDED:
            # Integration point #3 (§7 point 3, Phase 5) — same
            # publish-before-commit reasoning as publish_outcome above.
            await notification_producer.publish_payment_confirmed(payment.booking_id)
        await self._session.commit()

    async def refund_payment(
        self, booking_id: uuid.UUID, notification_producer: NotificationProducer
    ) -> None:
        """Cancellation-triggered refund (§22, integration point #5) —
        called from BookingCancelledConsumer, no equivalent API route (this
        service has no access to booking_db to check ownership, §8; the
        booking.cancelled message is itself the authorization — Booking
        Service already enforced ownership before publishing it).

        Idempotency gate mirrors create_charge's "resubmit only if the
        provider-side ID column is still NULL" pattern rather than a
        rowcount-gated status transition: a genuinely concurrent redelivery
        race isn't reachable here the way it was for the webhook route
        (this consumer processes one Kafka partition's records strictly
        sequentially), so only crash-then-restart redelivery is possible,
        not two overlapping deliveries — the same resubmission-gate
        reasoning already proven correct for charges applies unchanged.
        Two residual gaps, same risk tolerance this pattern already accepts
        for create_charge, not fixed here (found in code review): a
        consumer-group rebalance or a second running replica could still
        put two calls for one booking in flight, resting correctness
        entirely on Stripe's own `{booking_id}-refund` idempotency key as
        the backstop — same as create_charge's own documented residual
        race; and that idempotency key's protection is time-boxed to
        Stripe's own ~24h key-expiry window, not indefinite, if a crash
        between Stripe accepting the refund and this method's commit is
        followed by redelivery arriving after that window closes."""
        payment = await self._payments.get_by_booking_id(booking_id)
        if payment is None:
            logger.warning("refund_payment_not_found", booking_id=str(booking_id))
            return
        if payment.stripe_refund_id is not None:
            logger.info("refund_replay_no_op", booking_id=str(booking_id))
            return
        if payment.status is not PaymentStatus.SUCCEEDED:
            logger.warning(
                "refund_payment_unexpected_status", booking_id=str(booking_id), status=payment.status.value
            )
            return
        await self._submit_refund_to_stripe(payment, notification_producer)

    async def _submit_refund_to_stripe(self, payment: Payment, notification_producer: NotificationProducer) -> None:
        try:
            refund = await stripe.Refund.create_async(
                payment_intent=payment.stripe_charge_id,
                idempotency_key=f"{payment.booking_id}-refund",
            )
        except stripe.error.StripeError as exc:
            # Refund-failure path (§22's explicit scope boundary — no
            # re-lock, no rollback): logged as a warning, not an error —
            # this is Stripe/the card network declining, an expected,
            # handled failure per the log-level-discipline convention, not
            # a system incident. Payment.status stays SUCCEEDED so a future
            # redelivery or manual retry can still attempt the refund
            # again; do not re-raise, a Kafka consumer's per-message
            # exception handling shouldn't kill the background consumer
            # task over a Stripe-side failure that's already been surfaced.
            logger.warning("stripe_refund_submission_failed", booking_id=str(payment.booking_id), error=str(exc))
            await self._publish_refund_failed_notification(payment.booking_id, str(exc), notification_producer)
            return
        payment.stripe_refund_id = refund.id
        payment.status = PaymentStatus.REFUNDED
        await self._session.commit()

    async def _publish_refund_failed_notification(
        self, booking_id: uuid.UUID, reason: str, notification_producer: NotificationProducer
    ) -> None:
        # A failure here (broker down, etc.) must not escape and be
        # misattributed by the consumer's retry wrapper as a DB-write
        # failure (found in code review) — it would trigger a pointless
        # re-submission to Stripe on retry (safe, same idempotency key, but
        # wasteful) and, after the retry budget is exhausted, silently lose
        # the notification with a misleading log event name. Logged and
        # swallowed instead: the refund failure itself is already recorded
        # by the warning above; losing only the notification is the
        # narrower, honestly-scoped failure.
        try:
            await notification_producer.publish_refund_failed(booking_id, reason)
        except Exception:
            logger.error("refund_failed_notification_publish_failed", booking_id=str(booking_id), exc_info=True)
