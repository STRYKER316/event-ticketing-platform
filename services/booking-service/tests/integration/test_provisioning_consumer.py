import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Event, Ticket
from app.kafka import consumers
from app.kafka.consumers import ProvisioningConsumer
from app.kafka.schemas import EventSeat, EventUpsertedMessage, KafkaAction

pytestmark = pytest.mark.asyncio


def _message(event_id: uuid.UUID) -> bytes:
    start = datetime.now(timezone.utc) + timedelta(days=1)
    msg = EventUpsertedMessage(
        action=KafkaAction.UPSERTED,
        event_id=event_id,
        title="Provisioning Test",
        description=None,
        start_time=start,
        end_time=start + timedelta(hours=2),
        venue_name="Test Arena",
        performer_names=[],
        seats=[
            EventSeat(section="A", row="1", label="A1", price_cents=2500),
            EventSeat(section="A", row="1", label="A2", price_cents=2500),
        ],
    )
    return msg.model_dump_json().encode()


async def _count_tickets(session: AsyncSession, event_id: uuid.UUID) -> int:
    result = await session.execute(select(func.count()).select_from(Ticket).where(Ticket.event_id == event_id))
    return result.scalar_one()


async def test_upserted_message_provisions_one_ticket_per_seat(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    event_id = uuid.uuid4()
    consumer = ProvisioningConsumer(consumer=None, session_factory=db_session_factory)

    await consumer._handle(_message(event_id))

    async with db_session_factory() as session:
        assert await _count_tickets(session, event_id) == 2
        tickets = (await session.execute(select(Ticket).where(Ticket.event_id == event_id))).scalars().all()
        assert all(ticket.price_cents == 2500 for ticket in tickets)


async def test_upserted_message_writes_the_event_start_time(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    # The cancellation-cutoff check depends on this row, written from the same message that already provisions tickets (decisions-log §22 amendment #2).
    event_id = uuid.uuid4()
    consumer = ProvisioningConsumer(consumer=None, session_factory=db_session_factory)

    await consumer._handle(_message(event_id))

    async with db_session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None
        assert event.start_time is not None


async def test_republished_event_upserts_a_corrected_start_time(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    # ON CONFLICT DO UPDATE, not DO NOTHING (unlike the ticket insert) — a
    # republished event's corrected start_time must stay current.
    event_id = uuid.uuid4()
    consumer = ProvisioningConsumer(consumer=None, session_factory=db_session_factory)
    await consumer._handle(_message(event_id))

    corrected_start = datetime.now(timezone.utc) + timedelta(days=30)
    msg = EventUpsertedMessage(
        action=KafkaAction.UPSERTED,
        event_id=event_id,
        title="Provisioning Test",
        description=None,
        start_time=corrected_start,
        end_time=corrected_start + timedelta(hours=2),
        venue_name="Test Arena",
        performer_names=[],
        seats=[EventSeat(section="A", row="1", label="A1", price_cents=2500)],
    )
    await consumer._handle(msg.model_dump_json().encode())

    async with db_session_factory() as session:
        event = await session.get(Event, event_id)
        assert abs((event.start_time - corrected_start).total_seconds()) < 1


async def test_redelivered_message_creates_no_duplicate_tickets(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    event_id = uuid.uuid4()
    consumer = ProvisioningConsumer(consumer=None, session_factory=db_session_factory)
    raw = _message(event_id)

    await consumer._handle(raw)
    await consumer._handle(raw)  # redelivery

    async with db_session_factory() as session:
        assert await _count_tickets(session, event_id) == 2


async def test_seat_map_larger_than_one_insert_batch_provisions_every_seat(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    # Regression: an unbatched multi-row INSERT overflowed Postgres's bind-param cap near event-service's seat-map limit; 6000 seats forces a real two-batch provision.
    event_id = uuid.uuid4()
    seat_count = 6000
    start = datetime.now(timezone.utc) + timedelta(days=1)
    msg = EventUpsertedMessage(
        action=KafkaAction.UPSERTED,
        event_id=event_id,
        title="Large Venue Test",
        description=None,
        start_time=start,
        end_time=start + timedelta(hours=2),
        venue_name="Huge Arena",
        performer_names=[],
        seats=[EventSeat(section="A", row=str(i), label=f"A{i}", price_cents=2500) for i in range(seat_count)],
    )
    consumer = ProvisioningConsumer(consumer=None, session_factory=db_session_factory)

    await consumer._handle(msg.model_dump_json().encode())

    async with db_session_factory() as session:
        assert await _count_tickets(session, event_id) == seat_count


async def test_transient_db_failure_recovers_on_retry_and_still_provisions(
    db_session_factory: async_sessionmaker[AsyncSession], monkeypatch
):
    # A DB error on the first attempt(s) (the "connection blip" the retry loop exists for) must not cost the tickets.
    monkeypatch.setattr(consumers, "DB_WRITE_RETRY_BACKOFF_SECONDS", 0)
    event_id = uuid.uuid4()
    call_count = 0

    def flaky_session_factory():
        nonlocal call_count
        call_count += 1
        if call_count < consumers.DB_WRITE_MAX_ATTEMPTS:
            raise RuntimeError("simulated transient DB failure")
        return db_session_factory()

    consumer = ProvisioningConsumer(consumer=None, session_factory=flaky_session_factory)

    await consumer._handle(_message(event_id))

    assert call_count == consumers.DB_WRITE_MAX_ATTEMPTS
    async with db_session_factory() as session:
        assert await _count_tickets(session, event_id) == 2
