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
    create_mock = MagicMock(return_value=fake_intent)
    monkeypatch.setattr(stripe.PaymentIntent, "create", create_mock)

    result = await manager.create_charge(_payload(booking_id))

    assert result.status is PaymentStatus.PENDING
    create_mock.assert_called_once()
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

    create_mock = MagicMock()
    monkeypatch.setattr(stripe.PaymentIntent, "create", create_mock)

    first = await manager.create_charge(_payload(booking_id))
    second = await manager.create_charge(_payload(booking_id))

    create_mock.assert_not_called()
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
    create_mock = MagicMock(return_value=fake_intent)
    monkeypatch.setattr(stripe.PaymentIntent, "create", create_mock)

    result = await manager.create_charge(_payload(booking_id))

    create_mock.assert_called_once()
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
    payments = AsyncMock(get_by_stripe_charge_id=AsyncMock(return_value=payment))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    producer = AsyncMock()

    await manager.handle_webhook_event(_webhook_event("payment_intent.succeeded", "pi_123"), producer)

    assert payment.status is PaymentStatus.SUCCEEDED
    producer.publish_outcome.assert_awaited_once_with(payment)


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
    payments = AsyncMock(get_by_stripe_charge_id=AsyncMock(return_value=payment))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    producer = AsyncMock()

    await manager.handle_webhook_event(_webhook_event("payment_intent.succeeded", "pi_123"), producer)

    assert payment.status is PaymentStatus.SUCCEEDED
    producer.publish_outcome.assert_not_awaited()


async def test_webhook_for_unknown_charge_is_a_safe_no_op():
    payments = AsyncMock(get_by_stripe_charge_id=AsyncMock(return_value=None))
    manager = PaymentManager(session=AsyncMock(), payments=payments)
    producer = AsyncMock()

    await manager.handle_webhook_event(_webhook_event("payment_intent.succeeded", "pi_unknown"), producer)

    producer.publish_outcome.assert_not_awaited()


async def test_create_charge_raises_502_when_stripe_unreachable(monkeypatch):
    booking_id = uuid.uuid4()
    payments = AsyncMock(get_by_booking_id=AsyncMock(return_value=None), create=AsyncMock(side_effect=_stamp_generated_fields))
    manager = PaymentManager(session=AsyncMock(), payments=payments)

    def _raise(*args, **kwargs):
        raise stripe.error.APIConnectionError("boom")

    monkeypatch.setattr(stripe.PaymentIntent, "create", _raise)

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_charge(_payload(booking_id))
    assert exc_info.value.status_code == 502
