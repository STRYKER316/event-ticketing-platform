import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from shared_auth import Principal
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.booking_repository import BookingRepository
from app.db.event_repository import EventRepository
from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository
from app.logic.booking_manager import BookingManager
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy

from .conftest import seed_ticket

pytestmark = pytest.mark.asyncio

USER = Principal(subject="user-1", roles=[])


async def test_pay_booking_after_hold_expired_via_real_sweep_409s_without_charging(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    # Proves the expired-hold-race guard against a real expiry, not a
    # mocked booking.status — seeds a genuinely stale PENDING booking, runs
    # the real sweep mechanism (BookingRepository.expire_stale_pending(),
    # the same call hold_sweep.py's scheduled job makes) so the booking
    # actually transitions PENDING -> EXPIRED, then attempts payment.
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.HELD
        booking = Booking(
            user_subject=USER.subject,
            event_id=uuid.uuid4(),
            ticket_id=ticket_id,
            status=BookingStatus.PENDING,
            created_at=datetime.now(timezone.utc) - timedelta(seconds=700),
        )
        session.add(booking)
        await session.commit()
        booking_id = booking.id

    async with db_session_factory() as session:
        expired = await BookingRepository(session).expire_stale_pending(older_than_seconds=600)
        await session.commit()
    assert expired == 1

    http_client = AsyncMock()
    async with db_session_factory() as session:
        manager = BookingManager(
            session=session,
            tickets=TicketRepository(session),
            bookings=BookingRepository(session),
            hold_strategy=CronHoldStrategy(session),
            events=EventRepository(session),
        )
        with pytest.raises(HTTPException) as exc_info:
            await manager.pay_booking(USER, booking_id, "token", http_client)
    assert exc_info.value.status_code == 409
    http_client.post.assert_not_awaited()

    async with db_session_factory() as session:
        booking = await session.get(Booking, booking_id)
        assert booking.status is BookingStatus.EXPIRED
