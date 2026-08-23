import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.booking_repository import BookingRepository
from app.db.models import Booking, BookingStatus

from .conftest import seed_ticket

pytestmark = pytest.mark.asyncio


async def test_expire_stale_pending_flips_old_pending_bookings_to_expired(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        stale = Booking(
            user_subject="user-1",
            event_id=uuid.uuid4(),
            ticket_id=ticket_id,
            status=BookingStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=700),
        )
        session.add(stale)
        await session.commit()
        booking_id = stale.id

    async with db_session_factory() as session:
        expired = await BookingRepository(session).expire_stale_pending(older_than_seconds=600)
        await session.commit()
    assert expired == 1

    async with db_session_factory() as session:
        booking = await session.get(Booking, booking_id)
        assert booking.status is BookingStatus.EXPIRED


async def test_expire_stale_pending_does_not_touch_recent_pending_bookings(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        fresh = Booking(
            user_subject="user-1", event_id=uuid.uuid4(), ticket_id=ticket_id, status=BookingStatus.PENDING
        )
        session.add(fresh)
        await session.commit()
        booking_id = fresh.id

    async with db_session_factory() as session:
        expired = await BookingRepository(session).expire_stale_pending(older_than_seconds=600)
        await session.commit()
    assert expired == 0

    async with db_session_factory() as session:
        booking = await session.get(Booking, booking_id)
        assert booking.status is BookingStatus.PENDING


async def test_abandoned_redis_hold_no_longer_permanently_blocks_the_seat(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    # Bug this closes: under redis strategy, an abandoned PENDING booking used to sit forever, permanently blocking the seat via uq_bookings_active_ticket.
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        abandoned = Booking(
            user_subject="user-1",
            event_id=uuid.uuid4(),
            ticket_id=ticket_id,
            status=BookingStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=700),
        )
        session.add(abandoned)
        await session.commit()

    # Before the sweep runs, a new booking on the same ticket is still correctly rejected.
    async with db_session_factory() as session:
        blocked = Booking(
            user_subject="user-2", event_id=uuid.uuid4(), ticket_id=ticket_id, status=BookingStatus.PENDING
        )
        session.add(blocked)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with db_session_factory() as session:
        await BookingRepository(session).expire_stale_pending(older_than_seconds=600)
        await session.commit()

    async with db_session_factory() as session:
        new_booking = Booking(
            user_subject="user-2", event_id=uuid.uuid4(), ticket_id=ticket_id, status=BookingStatus.PENDING
        )
        session.add(new_booking)
        await session.commit()  # must not raise now
