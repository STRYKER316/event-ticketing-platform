import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.booking_repository import BookingRepository
from app.db.event_repository import EventRepository
from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository
from app.logic.booking_manager import BookingManager
from app.logic.helpers.redis_hold_strategy import RedisHoldStrategy

pytestmark = pytest.mark.asyncio


async def test_list_tickets_for_event_reports_real_status_under_redis_strategy(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: Redis
):
    # Regression: under redis strategy, RedisHoldStrategy never writes tickets.status (§6), so a raw-column read reported every held/booked seat as AVAILABLE.
    event_id = uuid.uuid4()
    async with db_session_factory() as session:
        available_ticket = Ticket(
            event_id=event_id, section="A", row_name="1", seat_label="1", price_cents=1000, status=TicketStatus.AVAILABLE
        )
        held_ticket = Ticket(
            event_id=event_id, section="A", row_name="1", seat_label="2", price_cents=1000, status=TicketStatus.AVAILABLE
        )
        booked_ticket = Ticket(
            event_id=event_id, section="A", row_name="1", seat_label="3", price_cents=1000, status=TicketStatus.AVAILABLE
        )
        session.add_all([available_ticket, held_ticket, booked_ticket])
        await session.flush()
        # A CONFIRMED booking is the only source of truth for BOOKED here — tickets.status is left AVAILABLE on purpose under the redis strategy.
        session.add(
            Booking(
                user_subject="user-1",
                event_id=event_id,
                ticket_id=booked_ticket.id,
                status=BookingStatus.CONFIRMED,
            )
        )
        await session.commit()
        available_id, held_id, booked_id = available_ticket.id, held_ticket.id, booked_ticket.id

    # Held purely in Redis — tickets.status for this row is untouched.
    strategy = RedisHoldStrategy(redis_client)
    await strategy.acquire_hold(held_id, ttl_seconds=60)

    async with db_session_factory() as session:
        manager = BookingManager(
            session=session,
            tickets=TicketRepository(session),
            bookings=BookingRepository(session),
            hold_strategy=RedisHoldStrategy(redis_client),
            events=EventRepository(session),
        )
        results = await manager.list_tickets_for_event(event_id)

    statuses = {r.ticket_id: r.status for r in results}
    assert statuses[available_id] is TicketStatus.AVAILABLE
    assert statuses[held_id] is TicketStatus.HELD
    assert statuses[booked_id] is TicketStatus.BOOKED
