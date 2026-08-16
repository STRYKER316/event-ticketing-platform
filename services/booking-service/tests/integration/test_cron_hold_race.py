import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy

from .conftest import seed_ticket

pytestmark = pytest.mark.asyncio


async def test_exactly_one_winner_under_concurrent_acquire_real_postgres(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    ticket_id = await seed_ticket(db_session_factory)

    async def attempt() -> bool:
        async with db_session_factory() as session:
            acquired = await CronHoldStrategy(session).acquire_hold(ticket_id, ttl_seconds=60)
            await session.commit()
            return acquired

    results = await asyncio.gather(*(attempt() for _ in range(20)))
    assert sum(results) == 1

    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket.status is TicketStatus.HELD


async def test_abandoned_hold_auto_releases_on_sweep(db_session_factory: async_sessionmaker[AsyncSession]):
    async with db_session_factory() as session:
        ticket = Ticket(
            event_id=uuid.uuid4(),
            section="A",
            row_name="1",
            seat_label="A1",
            status=TicketStatus.HELD,
            hold_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),  # already expired
        )
        session.add(ticket)
        await session.flush()
        booking = Booking(
            user_subject="user-1", event_id=ticket.event_id, ticket_id=ticket.id, status=BookingStatus.PENDING
        )
        session.add(booking)
        await session.commit()
        ticket_id, booking_id = ticket.id, booking.id

    async with db_session_factory() as session:
        released = await CronHoldStrategy(session).release_expired()
        await session.commit()
    assert released == 1

    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        booking = await session.get(Booking, booking_id)
        assert ticket.status is TicketStatus.AVAILABLE
        assert ticket.hold_expires_at is None
        assert booking.status is BookingStatus.EXPIRED


async def test_sweep_does_not_touch_unexpired_holds(db_session_factory: async_sessionmaker[AsyncSession]):
    async with db_session_factory() as session:
        ticket = Ticket(
            event_id=uuid.uuid4(),
            section="A",
            row_name="1",
            seat_label="A1",
            status=TicketStatus.HELD,
            hold_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        session.add(ticket)
        await session.commit()
        ticket_id = ticket.id

    async with db_session_factory() as session:
        released = await CronHoldStrategy(session).release_expired()
        await session.commit()
    assert released == 0

    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket.status is TicketStatus.HELD


async def test_release_hold_returns_ticket_to_available(db_session_factory: async_sessionmaker[AsyncSession]):
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        strategy = CronHoldStrategy(session)
        await strategy.acquire_hold(ticket_id, ttl_seconds=60)
        await session.commit()

    async with db_session_factory() as session:
        strategy = CronHoldStrategy(session)
        await strategy.release_hold(ticket_id)
        await session.commit()

    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket.status is TicketStatus.AVAILABLE
        assert ticket.hold_expires_at is None
