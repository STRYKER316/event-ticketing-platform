import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChargeRequest
from app.db.models import Payment, PaymentStatus
from app.db.payment_repository import PaymentRepository
from app.logic.payment_manager import PaymentManager

pytestmark = pytest.mark.asyncio


async def test_create_charge_persists_pending_payment_with_correct_amount(db_session: AsyncSession, monkeypatch):
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(stripe.PaymentIntent, "create", MagicMock(return_value=MagicMock(id="pi_test_1")))
    manager = PaymentManager(session=db_session, payments=PaymentRepository(db_session))

    result = await manager.create_charge(
        ChargeRequest(booking_id=booking_id, ticket_id=ticket_id, amount_cents=5000, currency="usd")
    )

    assert result.status is PaymentStatus.PENDING
    assert result.amount_cents == 5000
    persisted = await PaymentRepository(db_session).get_by_booking_id(booking_id)
    assert persisted is not None
    assert persisted.stripe_charge_id == "pi_test_1"


async def test_replayed_charge_against_real_db_does_not_double_charge(db_session: AsyncSession, monkeypatch):
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    create_mock = MagicMock(return_value=MagicMock(id="pi_test_2"))
    monkeypatch.setattr(stripe.PaymentIntent, "create", create_mock)
    manager = PaymentManager(session=db_session, payments=PaymentRepository(db_session))
    payload = ChargeRequest(booking_id=booking_id, ticket_id=ticket_id, amount_cents=5000, currency="usd")

    first = await manager.create_charge(payload)
    second = await manager.create_charge(payload)

    create_mock.assert_called_once()
    assert first.id == second.id


async def test_webhook_transitions_payment_and_replay_is_a_safe_no_op(db_session: AsyncSession):
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    payments = PaymentRepository(db_session)
    await payments.create(
        Payment(
            booking_id=booking_id,
            ticket_id=ticket_id,
            amount_cents=5000,
            currency="usd",
            stripe_charge_id="pi_webhook_test",
            idempotency_key=str(booking_id),
        )
    )
    await db_session.commit()
    manager = PaymentManager(session=db_session, payments=payments)

    producer = AsyncMock()
    event = {"type": "payment_intent.succeeded", "data": {"object": {"id": "pi_webhook_test"}}}

    await manager.handle_webhook_event(event, producer)
    await manager.handle_webhook_event(event, producer)  # redelivery

    producer.publish_outcome.assert_awaited_once()
    persisted = await PaymentRepository(db_session).get_by_booking_id(booking_id)
    assert persisted.status is PaymentStatus.SUCCEEDED
