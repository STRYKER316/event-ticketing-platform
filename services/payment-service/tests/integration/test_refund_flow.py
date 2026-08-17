import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Payment, PaymentStatus
from app.db.payment_repository import PaymentRepository
from app.kafka import consumers as consumers_module
from app.kafka.consumers import BookingCancelledConsumer
from app.kafka.schemas import BookingCancelledMessage
from app.logic.payment_manager import PaymentManager

pytestmark = pytest.mark.asyncio


async def _seed_succeeded_payment(session: AsyncSession, booking_id: uuid.UUID, stripe_charge_id: str) -> None:
    await PaymentRepository(session).create(
        Payment(
            booking_id=booking_id,
            ticket_id=uuid.uuid4(),
            amount_cents=2500,
            currency="usd",
            status=PaymentStatus.SUCCEEDED,
            stripe_charge_id=stripe_charge_id,
            idempotency_key=str(booking_id),
        )
    )
    await session.commit()


async def test_refund_payment_persists_refunded_status_and_stripe_refund_id(
    db_session: AsyncSession, monkeypatch
):
    booking_id = uuid.uuid4()
    await _seed_succeeded_payment(db_session, booking_id, "pi_refund_test_1")
    monkeypatch.setattr(stripe.Refund, "create_async", AsyncMock(return_value=MagicMock(id="re_test_1")))
    manager = PaymentManager(session=db_session, payments=PaymentRepository(db_session))

    await manager.refund_payment(booking_id, AsyncMock())

    persisted = await PaymentRepository(db_session).get_by_booking_id(booking_id)
    assert persisted.status is PaymentStatus.REFUNDED
    assert persisted.stripe_refund_id == "re_test_1"


async def test_refund_payment_replay_against_real_db_does_not_double_refund(
    db_session: AsyncSession, monkeypatch
):
    booking_id = uuid.uuid4()
    await _seed_succeeded_payment(db_session, booking_id, "pi_refund_test_2")
    refund_mock = AsyncMock(return_value=MagicMock(id="re_test_2"))
    monkeypatch.setattr(stripe.Refund, "create_async", refund_mock)
    manager = PaymentManager(session=db_session, payments=PaymentRepository(db_session))

    await manager.refund_payment(booking_id, AsyncMock())
    await manager.refund_payment(booking_id, AsyncMock())  # redelivery

    refund_mock.assert_awaited_once()


async def test_booking_cancelled_consumer_redelivery_is_a_safe_no_op(
    db_session_factory: async_sessionmaker[AsyncSession], monkeypatch
):
    booking_id = uuid.uuid4()
    async with db_session_factory() as seed_session:
        await _seed_succeeded_payment(seed_session, booking_id, "pi_refund_test_3")

    refund_mock = AsyncMock(return_value=MagicMock(id="re_test_3"))
    monkeypatch.setattr(stripe.Refund, "create_async", refund_mock)
    monkeypatch.setattr(consumers_module, "get_notification_producer", AsyncMock(return_value=AsyncMock()))

    consumer = BookingCancelledConsumer(consumer=None, session_factory=db_session_factory)
    raw = BookingCancelledMessage(booking_id=booking_id).model_dump_json().encode()

    await consumer._handle(raw)
    await consumer._handle(raw)  # redelivery

    refund_mock.assert_awaited_once()
    async with db_session_factory() as session:
        persisted = await PaymentRepository(session).get_by_booking_id(booking_id)
        assert persisted.status is PaymentStatus.REFUNDED


async def test_booking_cancelled_consumer_stripe_failure_leaves_payment_succeeded(
    db_session_factory: async_sessionmaker[AsyncSession], monkeypatch
):
    booking_id = uuid.uuid4()
    async with db_session_factory() as seed_session:
        await _seed_succeeded_payment(seed_session, booking_id, "pi_refund_test_4")

    async def _raise(*args, **kwargs):
        raise stripe.error.APIConnectionError("boom")

    monkeypatch.setattr(stripe.Refund, "create_async", _raise)
    notification_producer = AsyncMock()
    monkeypatch.setattr(consumers_module, "get_notification_producer", AsyncMock(return_value=notification_producer))

    consumer = BookingCancelledConsumer(consumer=None, session_factory=db_session_factory)
    raw = BookingCancelledMessage(booking_id=booking_id).model_dump_json().encode()

    await consumer._handle(raw)

    notification_producer.publish_refund_failed.assert_awaited_once()
    async with db_session_factory() as session:
        persisted = await PaymentRepository(session).get_by_booking_id(booking_id)
        assert persisted.status is PaymentStatus.SUCCEEDED
        assert persisted.stripe_refund_id is None
