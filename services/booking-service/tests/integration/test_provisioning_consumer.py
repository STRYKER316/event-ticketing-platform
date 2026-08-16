import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Ticket
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
        seats=[EventSeat(section="A", row="1", label="A1"), EventSeat(section="A", row="1", label="A2")],
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
