import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from shared_auth import Principal

from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.logic.booking_manager import BookingManager
from app.logic.helpers.fake_hold_strategy import FakeHoldStrategy

pytestmark = pytest.mark.asyncio

USER = Principal(subject="user-1", roles=[])


def _stamp_generated_fields(booking: Booking) -> Booking:
    # Simulates what BaseRepository.create()'s real flush() does: populates
    # the client-side `id` default and (via implicit RETURNING) the
    # server-side `created_at` default — neither of which a bare AsyncMock
    # would ever set.
    booking.id = uuid.uuid4()
    booking.created_at = datetime.now(timezone.utc)
    return booking


async def test_create_booking_happy_path():
    ticket_id = uuid.uuid4()
    event_id = uuid.uuid4()
    ticket = Ticket(id=ticket_id, event_id=event_id, section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.AVAILABLE)

    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(create=AsyncMock(side_effect=_stamp_generated_fields)),
        hold_strategy=FakeHoldStrategy(),
    )

    result = await manager.create_booking(USER, ticket_id)

    assert result.status is BookingStatus.PENDING
    assert result.ticket_id == ticket_id
    assert result.user_subject == USER.subject
    assert result.event_id == event_id


async def test_create_booking_on_unknown_ticket_404s():
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=None)),
        bookings=AsyncMock(),
        hold_strategy=FakeHoldStrategy(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_booking(USER, uuid.uuid4())
    assert exc_info.value.status_code == 404


async def test_create_booking_on_already_booked_ticket_409s():
    ticket_id = uuid.uuid4()
    ticket = Ticket(id=ticket_id, event_id=uuid.uuid4(), section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.BOOKED)
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(),
        hold_strategy=FakeHoldStrategy(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_booking(USER, ticket_id)
    assert exc_info.value.status_code == 409


async def test_create_booking_when_hold_already_taken_409s():
    ticket_id = uuid.uuid4()
    ticket = Ticket(id=ticket_id, event_id=uuid.uuid4(), section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.AVAILABLE)
    strategy = FakeHoldStrategy()
    await strategy.acquire_hold(ticket_id, ttl_seconds=60)  # pre-held by someone else

    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(),
        hold_strategy=strategy,
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_booking(USER, ticket_id)
    assert exc_info.value.status_code == 409
