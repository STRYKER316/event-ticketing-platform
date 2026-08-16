import uuid

import structlog
from fastapi import HTTPException, status
from shared_auth import Principal
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import BookingResponse
from app.core import get_settings
from app.db.booking_repository import BookingRepository
from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository
from app.logic.helpers.hold_strategy import TicketHoldStrategy

logger = structlog.get_logger()


class BookingManager:
    def __init__(
        self,
        session: AsyncSession,
        tickets: TicketRepository,
        bookings: BookingRepository,
        hold_strategy: TicketHoldStrategy,
    ):
        self._session = session
        self._tickets = tickets
        self._bookings = bookings
        self._hold_strategy = hold_strategy

    async def create_booking(self, user: Principal, ticket_id: uuid.UUID) -> BookingResponse:
        ticket = await self._fetch_bookable_ticket(ticket_id)
        await self._acquire_hold(ticket)
        booking = await self._create_booking_row(user, ticket)
        return self._build_response(booking)

    async def _fetch_bookable_ticket(self, ticket_id: uuid.UUID) -> Ticket:
        ticket = await self._tickets.get_by_id(ticket_id)
        if ticket is None:
            logger.warning("booking_ticket_not_found", ticket_id=str(ticket_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
        if ticket.status is TicketStatus.BOOKED:
            logger.warning("booking_ticket_already_booked", ticket_id=str(ticket_id))
            raise HTTPException(status.HTTP_409_CONFLICT, "seat already booked")
        return ticket

    async def _acquire_hold(self, ticket: Ticket) -> None:
        acquired = await self._hold_strategy.acquire_hold(ticket.id, get_settings().hold_ttl_seconds)
        if not acquired:
            logger.warning("booking_hold_acquire_failed", ticket_id=str(ticket.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "seat unavailable")

    async def _create_booking_row(self, user: Principal, ticket: Ticket) -> Booking:
        booking = Booking(
            user_subject=user.subject, event_id=ticket.event_id, ticket_id=ticket.id, status=BookingStatus.PENDING
        )
        try:
            await self._bookings.create(booking)
            await self._session.commit()
        except IntegrityError:
            # The partial unique index (uq_bookings_active_ticket) caught a
            # race the hold strategy somehow missed — defense-in-depth, not
            # the primary correctness mechanism. Compensate by releasing the
            # hold we just (wrongly) acquired, not by attempting a
            # distributed rollback (no distributed transactions, §8).
            await self._session.rollback()
            await self._hold_strategy.release_hold(ticket.id)
            logger.error("booking_integrity_race_lost", ticket_id=str(ticket.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "seat unavailable") from None
        return booking

    def _build_response(self, booking: Booking) -> BookingResponse:
        return BookingResponse.model_validate(booking)
