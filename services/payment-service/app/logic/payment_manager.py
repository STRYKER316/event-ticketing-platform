import stripe
import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChargeRequest, PaymentResponse
from app.db.models import Payment, PaymentStatus
from app.db.payment_repository import PaymentRepository
from app.kafka.producers import PaymentOutcomeProducer

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
        existing = await self._payments.get_by_booking_id(payload.booking_id)
        if existing is not None and existing.stripe_charge_id is not None:
            # Idempotent by construction (§9): a retried charge attempt for a
            # booking Stripe already accepted (stripe_charge_id set) returns
            # it as-is rather than calling Stripe again — Stripe's own
            # idempotency_key (booking ID) would also prevent a double
            # charge, but this avoids the extra API call and makes the no-op
            # explicit. A Payment row with no stripe_charge_id yet means the
            # previous attempt never actually reached Stripe (see
            # _submit_to_stripe's error path) — that case falls through
            # below and genuinely retries, rather than getting stuck replaying
            # a charge Stripe never received.
            logger.info("charge_idempotent_replay", booking_id=str(payload.booking_id))
            return self._build_response(existing)

        payment = existing or await self._create_pending_payment(payload)
        await self._submit_to_stripe(payment, payload)
        await self._session.commit()
        return self._build_response(payment)

    async def _create_pending_payment(self, payload: ChargeRequest) -> Payment:
        payment = Payment(
            booking_id=payload.booking_id,
            ticket_id=payload.ticket_id,
            amount_cents=payload.amount_cents,
            currency=payload.currency,
            idempotency_key=str(payload.booking_id),
        )
        await self._payments.create(payment)
        await self._session.commit()
        return payment

    async def _submit_to_stripe(self, payment: Payment, payload: ChargeRequest) -> None:
        # This call only *initiates* the charge attempt (§9 amendment) —
        # confirmation of success/failure is webhook-driven (see
        # payments.py's webhook route), even though Stripe test mode often
        # resolves a PaymentIntent synchronously. Treating the webhook as the
        # sole source of truth is what makes a lost synchronous response (a
        # network blip after Stripe already processed the charge) safe.
        try:
            intent = stripe.PaymentIntent.create(
                amount=payment.amount_cents,
                currency=payment.currency,
                payment_method=payload.payment_method,
                confirm=True,
                idempotency_key=payment.idempotency_key,
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            )
        except stripe.error.StripeError as exc:
            logger.error("stripe_charge_submission_failed", booking_id=str(payment.booking_id), error=str(exc))
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "payment provider unreachable") from exc
        payment.stripe_charge_id = intent.id

    def _build_response(self, payment: Payment) -> PaymentResponse:
        return PaymentResponse.model_validate(payment)

    async def handle_webhook_event(self, event: stripe.Event, producer: PaymentOutcomeProducer) -> None:
        """Webhook-driven confirmation (§9) — this is the sole source of
        truth for a Payment's terminal status, not create_charge()'s
        synchronous Stripe response. Idempotent by construction (§7's
        general rule, applied to a webhook the same as a Kafka consumer):
        only transitions a Payment that's still PENDING, so a redelivered
        webhook for an already-terminal Payment is a safe no-op."""
        new_status = _WEBHOOK_OUTCOME_BY_EVENT_TYPE.get(event["type"])
        if new_status is None:
            return
        intent_id = event["data"]["object"]["id"]
        payment = await self._payments.get_by_stripe_charge_id(intent_id)
        if payment is None:
            logger.warning("webhook_payment_not_found", stripe_charge_id=intent_id)
            return
        if payment.status is not PaymentStatus.PENDING:
            logger.info(
                "webhook_replay_no_op", booking_id=str(payment.booking_id), current_status=payment.status.value
            )
            return
        payment.status = new_status
        await self._session.commit()
        await producer.publish_outcome(payment)
