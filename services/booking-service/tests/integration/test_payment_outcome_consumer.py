import json
import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.kafka.consumers import PaymentOutcomeConsumer

from .conftest import seed_ticket

pytestmark = pytest.mark.asyncio


def _message(action: str, booking_id: uuid.UUID, ticket_id: uuid.UUID) -> bytes:
    return json.dumps({"action": action, "booking_id": str(booking_id), "ticket_id": str(ticket_id)}).encode()


async def _seed_pending_booking(session_factory: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID) -> uuid.UUID:
    async with session_factory() as session:
        booking = Booking(user_subject="user-1", event_id=uuid.uuid4(), ticket_id=ticket_id, status=BookingStatus.PENDING)
        session.add(booking)
        await session.commit()
        return booking.id


async def test_succeeded_message_confirms_booking_and_books_ticket_under_cron_strategy(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: Redis
):
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.HELD
        await session.commit()
    booking_id = await _seed_pending_booking(db_session_factory, ticket_id)
    consumer = PaymentOutcomeConsumer(consumer=None, session_factory=db_session_factory, redis=redis_client)

    await consumer._handle(_message("succeeded", booking_id, ticket_id))

    async with db_session_factory() as session:
        booking = await session.get(Booking, booking_id)
        ticket = await session.get(Ticket, ticket_id)
        assert booking.status is BookingStatus.CONFIRMED
        assert ticket.status is TicketStatus.BOOKED


async def test_failed_message_releases_hold_immediately_under_cron_strategy(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: Redis
):
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.HELD
        await session.commit()
    booking_id = await _seed_pending_booking(db_session_factory, ticket_id)
    consumer = PaymentOutcomeConsumer(consumer=None, session_factory=db_session_factory, redis=redis_client)

    await consumer._handle(_message("failed", booking_id, ticket_id))

    async with db_session_factory() as session:
        booking = await session.get(Booking, booking_id)
        ticket = await session.get(Ticket, ticket_id)
        assert booking.status is BookingStatus.EXPIRED
        assert ticket.status is TicketStatus.AVAILABLE


async def test_redelivered_message_on_already_confirmed_booking_does_not_touch_a_new_holder(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: Redis
):
    # The correctness contract this task exists to satisfy (§7): a
    # redelivered "failed" message for a booking that already succeeded
    # (already CONFIRMED) must not release the ticket a *different*, later
    # booking may since legitimately hold.
    ticket_id = await seed_ticket(db_session_factory)
    booking_id = await _seed_pending_booking(db_session_factory, ticket_id)
    consumer = PaymentOutcomeConsumer(consumer=None, session_factory=db_session_factory, redis=redis_client)
    await consumer._handle(_message("succeeded", booking_id, ticket_id))

    # A later booking now legitimately holds the same ticket.
    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.HELD
        await session.commit()

    await consumer._handle(_message("failed", booking_id, ticket_id))  # redelivered/stale message

    async with db_session_factory() as session:
        booking = await session.get(Booking, booking_id)
        ticket = await session.get(Ticket, ticket_id)
        assert booking.status is BookingStatus.CONFIRMED  # unchanged
        assert ticket.status is TicketStatus.HELD  # the later hold is untouched
