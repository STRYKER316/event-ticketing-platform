import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe
from fastapi import HTTPException

from app.api.schemas import ChargeRequest
from app.db.models import Payment, PaymentStatus
from app.logic.payment_manager import PaymentManager

pytestmark = pytest.mark.asyncio


def _payload(booking_id: uuid.UUID) -> ChargeRequest:
    return ChargeRequest(booking_id=booking_id, ticket_id=uuid.uuid4(), amount_cents=2500, currency="usd")


def _stamp_generated_fields(payment: Payment) -> Payment:
    payment.id = uuid.uuid4()
    payment.status = PaymentStatus.PENDING
    payment.created_at = datetime.now(timezone.utc)
    return payment


async def test_create_charge_calls_stripe_with_booking_id_as_idempotency_key(monkeypatch):
    booking_id = uuid.uuid4()
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=None), create=AsyncMock(side_effect=_stamp_generated_fields))
    manager = PaymentManager(session=AsyncMock(), payments=payments)

    fake_intent = MagicMock(id="pi_123")
    create_mock = AsyncMock(return_value=fake_intent)
    monkeypatch.setattr(stripe.PaymentIntent, "create_async", create_mock)

    result = await manager.create_charge(_payload(booking_id))

    assert result.status is PaymentStatus.PENDING
    create_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["idempotency_key"] == str(booking_id)


async def test_create_charge_is_idempotent_on_replay_once_stripe_accepted_it(monkeypatch):
    # The correctness contract this task exists to satisfy (§9): a repeated
    # charge attempt for a booking Stripe already accepted must not call
    # Stripe a second time.
    booking_id = uuid.uuid4()
    existing = Payment(
        id=uuid.uuid4(),
        booking_id=booking_id,
        ticket_id=uuid.uuid4(),
        amount_cents=2500,
        currency="usd",
        status=PaymentStatus.PENDING,
        stripe_charge_id="pi_already_submitted",
        idempotency_key=str(booking_id),
        created_at=datetime.now(timezone.utc),
    )
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=existing))
    manager = PaymentManager(session=AsyncMock(), payments=payments)

    create_mock = AsyncMock()
    monkeypatch.setattr(stripe.PaymentIntent, "create_async", create_mock)

    first = await manager.create_charge(_payload(booking_id))
    second = await manager.create_charge(_payload(booking_id))

    create_mock.assert_not_awaited()
    assert first.id == second.id == existing.id


async def test_create_charge_retries_stripe_when_previous_attempt_never_reached_it(monkeypatch):
    # Live-testing regression: a Payment row can exist with no
    # stripe_charge_id (the previous attempt raised before Stripe ever
    # responded, e.g. a transient network error) — that must be retried, not
    # treated as an idempotent replay forever.
    booking_id = uuid.uuid4()
    existing = Payment(
        id=uuid.uuid4(),
        booking_id=booking_id,
        ticket_id=uuid.uuid4(),
        amount_cents=2500,
        currency="usd",
        status=PaymentStatus.PENDING,
        stripe_charge_id=None,
        idempotency_key=str(booking_id),
        created_at=datetime.now(timezone.utc),
    )
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=existing))
    manager = PaymentManager(session=AsyncMock(), payments=payments)

    fake_intent = MagicMock(id="pi_now_succeeds")
    create_mock = AsyncMock(return_value=fake_intent)
    monkeypatch.setattr(stripe.PaymentIntent, "create_async", create_mock)

    result = await manager.create_charge(_payload(booking_id))

    create_mock.assert_awaited_once()
    assert result.id == existing.id
    assert existing.stripe_charge_id == "pi_now_succeeds"


def _webhook_event(event_type: str, intent_id: str) -> dict:
    return {"type": event_type, "data": {"object": {"id": intent_id}}}


async def test_webhook_success_transitions_pending_payment_and_publishes():
    payment = Payment(
        id=uuid.uuid4(),
        booking_id=uuid.uuid4(),
        ticket_id=uuid.uuid4(),
        amount_cents=2500,
        currency="usd",
        status=PaymentStatus.PENDING,
        stripe_charge_id="pi_123",
        idempotency_key="k",
        created_at=datetime.now(timezone.utc),
    )
    payments = AsyncMock(
        get_by_stripe_charge_id=AsyncMock(return_value=payment), transition_if_pending=AsyncMock(return_value=True)
    )
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    producer = AsyncMock()
    notification_producer = AsyncMock()

    await manager.handle_webhook_event(
        _webhook_event("payment_intent.succeeded", "pi_123"), producer, notification_producer
    )

    assert payment.status is PaymentStatus.SUCCEEDED
    payments.transition_if_pending.assert_awaited_once_with("pi_123", PaymentStatus.SUCCEEDED)
    producer.publish_outcome.assert_awaited_once_with(payment)
    # Integration point #3 (§7 point 3, Phase 5).
    notification_producer.publish_payment_confirmed.assert_awaited_once_with(payment.booking_id)


async def test_replayed_webhook_on_already_terminal_payment_is_a_safe_no_op():
    # The correctness contract this task exists to satisfy (§7's general
    # idempotent-consumer rule, applied to a webhook): a redelivered event
    # for an already-terminal Payment must not transition it again or
    # publish a second Kafka message.
    payment = Payment(
        id=uuid.uuid4(),
        booking_id=uuid.uuid4(),
        ticket_id=uuid.uuid4(),
        amount_cents=2500,
        currency="usd",
        status=PaymentStatus.SUCCEEDED,
        stripe_charge_id="pi_123",
        idempotency_key="k",
        created_at=datetime.now(timezone.utc),
    )
    payments = AsyncMock(
        get_by_stripe_charge_id=AsyncMock(return_value=payment), transition_if_pending=AsyncMock(return_value=False)
    )
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    producer = AsyncMock()
    notification_producer = AsyncMock()

    await manager.handle_webhook_event(
        _webhook_event("payment_intent.succeeded", "pi_123"), producer, notification_producer
    )

    assert payment.status is PaymentStatus.SUCCEEDED
    producer.publish_outcome.assert_not_awaited()
    notification_producer.publish_payment_confirmed.assert_not_awaited()


async def test_webhook_for_unknown_charge_is_a_safe_no_op():
    payments = AsyncMock(get_by_stripe_charge_id=AsyncMock(return_value=None))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    producer = AsyncMock()
    notification_producer = AsyncMock()

    await manager.handle_webhook_event(
        _webhook_event("payment_intent.succeeded", "pi_unknown"), producer, notification_producer
    )

    producer.publish_outcome.assert_not_awaited()
    notification_producer.publish_payment_confirmed.assert_not_awaited()


async def test_create_charge_raises_502_when_stripe_unreachable(monkeypatch):
    booking_id = uuid.uuid4()
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=None), create=AsyncMock(side_effect=_stamp_generated_fields))
    manager = PaymentManager(session=AsyncMock(), payments=payments)

    async def _raise(*args, **kwargs):
        raise stripe.error.APIConnectionError("boom")

    monkeypatch.setattr(stripe.PaymentIntent, "create_async", _raise)

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_charge(_payload(booking_id))
    assert exc_info.value.status_code == 502


def _succeeded_payment(booking_id: uuid.UUID, stripe_charge_id: str = "pi_123") -> Payment:
    return Payment(
        id=uuid.uuid4(),
        booking_id=booking_id,
        ticket_id=uuid.uuid4(),
        amount_cents=2500,
        currency="usd",
        status=PaymentStatus.SUCCEEDED,
        stripe_charge_id=stripe_charge_id,
        idempotency_key=str(booking_id),
        stripe_refund_id=None,
        created_at=datetime.now(timezone.utc),
    )


async def test_refund_payment_happy_path_calls_stripe_with_booking_refund_idempotency_key(monkeypatch):
    booking_id = uuid.uuid4()
    payment = _succeeded_payment(booking_id)
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=payment))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    notification_producer = AsyncMock()

    fake_refund = MagicMock(id="re_123")
    refund_mock = AsyncMock(return_value=fake_refund)
    monkeypatch.setattr(stripe.Refund, "create_async", refund_mock)

    await manager.refund_payment(booking_id, notification_producer)

    refund_mock.assert_awaited_once()
    assert refund_mock.call_args.kwargs["idempotency_key"] == f"{booking_id}-refund"
    assert refund_mock.call_args.kwargs["payment_intent"] == "pi_123"
    assert payment.status is PaymentStatus.REFUNDED
    assert payment.stripe_refund_id == "re_123"
    notification_producer.publish_refund_failed.assert_not_awaited()


async def test_refund_payment_on_unknown_booking_is_a_safe_no_op():
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=None))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    notification_producer = AsyncMock()

    await manager.refund_payment(uuid.uuid4(), notification_producer)

    notification_producer.publish_refund_failed.assert_not_awaited()


async def test_refund_payment_replay_after_success_is_a_safe_no_op(monkeypatch):
    # The correctness contract this exists to satisfy (§7's general
    # idempotent-consumer rule, applied to a Kafka consumer instead of a
    # webhook): a redelivered booking.cancelled message for an
    # already-refunded Payment must not call Stripe a second time.
    booking_id = uuid.uuid4()
    payment = _succeeded_payment(booking_id)
    payment.stripe_refund_id = "re_already_done"
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=payment))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    notification_producer = AsyncMock()

    refund_mock = AsyncMock()
    monkeypatch.setattr(stripe.Refund, "create_async", refund_mock)

    await manager.refund_payment(booking_id, notification_producer)

    refund_mock.assert_not_awaited()


async def test_refund_payment_on_non_succeeded_payment_is_a_safe_no_op(monkeypatch):
    booking_id = uuid.uuid4()
    payment = _succeeded_payment(booking_id)
    payment.status = PaymentStatus.PENDING
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=payment))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    notification_producer = AsyncMock()

    refund_mock = AsyncMock()
    monkeypatch.setattr(stripe.Refund, "create_async", refund_mock)

    await manager.refund_payment(booking_id, notification_producer)

    refund_mock.assert_not_awaited()


async def test_refund_payment_on_stripe_failure_publishes_notification_and_leaves_status_unchanged(monkeypatch):
    # §22's explicit scope boundary: a failed refund is logged and surfaced,
    # not rolled back — Payment.status must stay SUCCEEDED, not flip to any
    # terminal-looking state, so a future retry can still attempt it again.
    booking_id = uuid.uuid4()
    payment = _succeeded_payment(booking_id)
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=payment))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    notification_producer = AsyncMock()

    async def _raise(*args, **kwargs):
        raise stripe.error.APIConnectionError("boom")

    monkeypatch.setattr(stripe.Refund, "create_async", _raise)

    await manager.refund_payment(booking_id, notification_producer)

    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.stripe_refund_id is None
    notification_producer.publish_refund_failed.assert_awaited_once()
    assert notification_producer.publish_refund_failed.call_args.args[0] == booking_id


async def test_refund_payment_notification_publish_failure_does_not_raise(monkeypatch):
    # Found in code review: if publish_refund_failed itself raises (broker
    # down), that must not escape refund_payment — a Kafka consumer's own
    # retry wrapper would otherwise misattribute it as a DB failure and
    # retry the whole operation (including a pointless re-submission to
    # Stripe) before silently losing the notification anyway.
    booking_id = uuid.uuid4()
    payment = _succeeded_payment(booking_id)
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=payment))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    notification_producer = AsyncMock(publish_refund_failed=AsyncMock(side_effect=RuntimeError("kafka down")))

    async def _raise(*args, **kwargs):
        raise stripe.error.APIConnectionError("boom")

    monkeypatch.setattr(stripe.Refund, "create_async", _raise)

    await manager.refund_payment(booking_id, notification_producer)  # must not raise

    assert payment.status is PaymentStatus.SUCCEEDED
