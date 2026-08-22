import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import ChargeRequest
from app.db.models import Payment, PaymentStatus
from app.db.payment_repository import PaymentRepository
from app.logic.payment_manager import PaymentManager

pytestmark = pytest.mark.asyncio


async def test_create_charge_persists_pending_payment_with_correct_amount(db_session: AsyncSession, monkeypatch):
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(stripe.PaymentIntent, "create_async", AsyncMock(return_value=MagicMock(id="pi_test_1")))
    manager = PaymentManager(session=db_session, payments=PaymentRepository(db_session))

    result = await manager.create_charge(
        ChargeRequest(booking_id=booking_id, ticket_id=ticket_id, amount_cents=5000, currency="usd")
    )

    assert result.status is PaymentStatus.PENDING
    assert result.amount_cents == 5000
    persisted = await PaymentRepository(db_session).get_by_booking_id(booking_id)
    assert persisted is not None
    assert persisted.stripe_charge_id == "pi_test_1"


async def test_concurrent_first_time_charges_for_one_booking_only_one_creates(
    db_session_factory: async_sessionmaker[AsyncSession], monkeypatch
):
    # The correctness contract _resolve_payment_row's IntegrityError handling
    # exists to satisfy (found missing in code review): two genuinely
    # concurrent first-charge attempts for the same booking must resolve to
    # one Payment row, not a 500 from an unhandled unique-constraint violation.
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    create_mock = AsyncMock(return_value=MagicMock(id="pi_race"))
    monkeypatch.setattr(stripe.PaymentIntent, "create_async", create_mock)
    payload = ChargeRequest(booking_id=booking_id, ticket_id=ticket_id, amount_cents=5000, currency="usd")

    async def _attempt() -> uuid.UUID:
        async with db_session_factory() as session:
            manager = PaymentManager(session=session, payments=PaymentRepository(session))
            result = await manager.create_charge(payload)
            return result.id

    first_id, second_id = await asyncio.gather(_attempt(), _attempt())

    # Exactly one Payment row for the booking — the unique index is the hard
    # guarantee. Stripe may occasionally still be called twice here (if the
    # loser re-fetches before the winner's own stripe_charge_id commits) —
    # that residual race is exactly why Stripe's own idempotency_key exists
    # as the backstop against an actual double charge (§9); this test's job
    # is proving the previously-unhandled IntegrityError no longer 500s.
    assert first_id == second_id
    assert create_mock.await_count in (1, 2)
    async with db_session_factory() as session:
        rows = (await session.execute(select(Payment).where(Payment.booking_id == booking_id))).scalars().all()
        assert len(rows) == 1


async def test_replayed_charge_against_real_db_does_not_double_charge(db_session: AsyncSession, monkeypatch):
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    create_mock = AsyncMock(return_value=MagicMock(id="pi_test_2"))
    monkeypatch.setattr(stripe.PaymentIntent, "create_async", create_mock)
    manager = PaymentManager(session=db_session, payments=PaymentRepository(db_session))
    payload = ChargeRequest(booking_id=booking_id, ticket_id=ticket_id, amount_cents=5000, currency="usd")

    first = await manager.create_charge(payload)
    second = await manager.create_charge(payload)

    create_mock.assert_awaited_once()
    assert first.id == second.id


async def test_concurrent_overlapping_webhook_deliveries_only_one_wins(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    # The correctness contract the rowcount-gated transition_if_pending
    # exists to satisfy (found missing in code review — the previous
    # read-then-write version could let two overlapping deliveries both
    # pass the PENDING check before either committed): two genuinely
    # concurrent webhook deliveries for the same charge must not both
    # transition the row or both publish.
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    async with db_session_factory() as seed_session:
        await PaymentRepository(seed_session).create(
            Payment(
                booking_id=booking_id,
                ticket_id=ticket_id,
                amount_cents=5000,
                currency="usd",
                stripe_charge_id="pi_race_test",
                idempotency_key=str(booking_id),
            )
        )
        await seed_session.commit()

    event = {"type": "payment_intent.succeeded", "data": {"object": {"id": "pi_race_test"}}}
    producers = [AsyncMock(), AsyncMock()]
    notification_producers = [AsyncMock(), AsyncMock()]

    async def _deliver(index: int) -> None:
        async with db_session_factory() as session:
            manager = PaymentManager(session=session, payments=PaymentRepository(session))
            await manager.handle_webhook_event(event, producers[index], notification_producers[index])

    await asyncio.gather(_deliver(0), _deliver(1))

    publish_calls = sum(1 for producer in producers if producer.publish_outcome.await_count > 0)
    assert publish_calls == 1

    async with db_session_factory() as session:
        persisted = await PaymentRepository(session).get_by_booking_id(booking_id)
        assert persisted.status is PaymentStatus.SUCCEEDED


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
    notification_producer = AsyncMock()
    event = {"type": "payment_intent.succeeded", "data": {"object": {"id": "pi_webhook_test"}}}

    await manager.handle_webhook_event(event, producer, notification_producer)
    await manager.handle_webhook_event(event, producer, notification_producer)  # redelivery

    producer.publish_outcome.assert_awaited_once()
    notification_producer.publish_payment_confirmed.assert_awaited_once()
    persisted = await PaymentRepository(db_session).get_by_booking_id(booking_id)
    assert persisted.status is PaymentStatus.SUCCEEDED


async def test_late_success_webhook_after_failure_confirms_booking_against_real_db(db_session: AsyncSession):
    # A late genuine success must still confirm the booking even though an
    # earlier webhook already moved the row to FAILED — transition_if_pending
    # alone would silently drop this as a no-op.
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    payments = PaymentRepository(db_session)
    await payments.create(
        Payment(
            booking_id=booking_id,
            ticket_id=ticket_id,
            amount_cents=5000,
            currency="usd",
            status=PaymentStatus.FAILED,
            stripe_charge_id="pi_late_success",
            idempotency_key=str(booking_id),
        )
    )
    await db_session.commit()
    manager = PaymentManager(session=db_session, payments=payments)

    producer = AsyncMock()
    notification_producer = AsyncMock()
    event = {"type": "payment_intent.succeeded", "data": {"object": {"id": "pi_late_success"}}}

    await manager.handle_webhook_event(event, producer, notification_producer)
    await manager.handle_webhook_event(event, producer, notification_producer)  # redelivery

    producer.publish_outcome.assert_awaited_once()
    notification_producer.publish_payment_confirmed.assert_awaited_once()
    persisted = await PaymentRepository(db_session).get_by_booking_id(booking_id)
    assert persisted.status is PaymentStatus.SUCCEEDED
