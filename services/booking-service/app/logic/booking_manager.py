import uuid
from datetime import datetime, timezone

import httpx
import structlog
from fastapi import HTTPException, status
from shared_auth import Principal
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import BookingPayResponse, BookingResponse
from app.core import get_settings
from app.db.booking_repository import BookingRepository
from app.db.event_repository import EventRepository
from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository
from app.kafka.producers import BookingCancelledProducer
from app.logic.helpers.hold_strategy import TicketHoldStrategy

logger = structlog.get_logger()


class BookingManager:
    def __init__(
        self,
        session: AsyncSession,
        tickets: TicketRepository,
        bookings: BookingRepository,
        hold_strategy: TicketHoldStrategy,
        events: EventRepository,
        cancelled_producer: BookingCancelledProducer | None = None,
    ):
        self._session = session
        self._tickets = tickets
        self._bookings = bookings
        self._hold_strategy = hold_strategy
        self._events = events
        # Only cancel_booking needs this — unlike `events` (cheap to always
        # construct), the producer requires an async Kafka connection, so
        # create_booking/pay_booking callers shouldn't be forced to pay for
        # or supply one.
        self._cancelled_producer = cancelled_producer

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
            logger.warning("booking_integrity_race_lost", ticket_id=str(ticket.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "seat unavailable") from None
        return booking

    def _build_response(self, booking: Booking) -> BookingResponse:
        return BookingResponse.model_validate(booking)

    async def pay_booking(
        self, user: Principal, booking_id: uuid.UUID, bearer_token: str, http_client: httpx.AsyncClient
    ) -> BookingPayResponse:
        """Booking Service fronts payment (decisions-log §9 amendment):
        ownership is checked here, where booking_db actually lives, then a
        synchronous call initiates the charge on Payment Service, which has
        no access to this database (§8) to check ownership itself."""
        booking = await self._fetch_owned_pending_booking(user, booking_id)
        ticket = await self._tickets.get_by_id(booking.ticket_id)
        return await self._charge_via_payment_service(booking, ticket, bearer_token, http_client)

    async def _fetch_owned_pending_booking(self, user: Principal, booking_id: uuid.UUID) -> Booking:
        booking = await self._bookings.get_by_id(booking_id)
        if booking is None:
            logger.warning("pay_booking_not_found", booking_id=str(booking_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
        if booking.user_subject != user.subject:
            logger.warning("pay_booking_ownership_denied", booking_id=str(booking_id), subject=user.subject)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not your booking")
        if booking.status is not BookingStatus.PENDING:
            logger.warning("pay_booking_not_pending", booking_id=str(booking_id), status=booking.status.value)
            raise HTTPException(status.HTTP_409_CONFLICT, "booking is not pending payment")
        return booking

    async def _charge_via_payment_service(
        self, booking: Booking, ticket: Ticket, bearer_token: str, http_client: httpx.AsyncClient
    ) -> BookingPayResponse:
        url = f"{get_settings().payment_service_url}/payments/charge"
        payload = {
            "booking_id": str(booking.id),
            "ticket_id": str(ticket.id),
            "amount_cents": ticket.price_cents,
            "currency": "usd",
        }
        try:
            response = await http_client.post(url, json=payload, headers={"Authorization": f"Bearer {bearer_token}"})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Payment Service was reachable and answered with a real error of
            # its own (e.g. its own 502 when Stripe is unreachable) — not the
            # same failure as a connection/timeout below (found in code
            # review: both used to collapse into the same misleading
            # "unreachable" message). Forward its status verbatim.
            logger.error(
                "pay_booking_payment_service_rejected",
                booking_id=str(booking.id),
                status_code=exc.response.status_code,
            )
            raise HTTPException(exc.response.status_code, "payment service rejected the charge attempt") from exc
        except httpx.HTTPError as exc:
            logger.error("pay_booking_payment_service_call_failed", booking_id=str(booking.id), error=str(exc))
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "payment service unreachable") from exc
        body = response.json()
        return BookingPayResponse(
            payment_id=body["id"], status=body["status"], amount_cents=body["amount_cents"], currency=body["currency"]
        )

    async def cancel_booking(self, user: Principal, booking_id: uuid.UUID) -> BookingResponse:
        """Owner-only, CONFIRMED-only, full-refund cancellation (§22).
        Optimistic immediate seat release — the ticket returns to AVAILABLE
        as part of this same call, not deferred to any later sweep."""
        booking = await self._fetch_owned_confirmed_booking(user, booking_id)
        await self._check_before_event_start(booking)
        await self._transition_and_release(booking)
        return self._build_response(booking)

    async def _fetch_owned_confirmed_booking(self, user: Principal, booking_id: uuid.UUID) -> Booking:
        booking = await self._bookings.get_by_id(booking_id)
        if booking is None:
            logger.warning("cancel_booking_not_found", booking_id=str(booking_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
        if booking.user_subject != user.subject:
            logger.warning("cancel_booking_ownership_denied", booking_id=str(booking_id), subject=user.subject)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not your booking")
        if booking.status is not BookingStatus.CONFIRMED:
            logger.warning("cancel_booking_not_confirmed", booking_id=str(booking_id), status=booking.status.value)
            raise HTTPException(status.HTTP_409_CONFLICT, "booking is not confirmed")
        return booking

    async def _check_before_event_start(self, booking: Booking) -> None:
        start_time = await self._events.get_start_time(booking.event_id)
        if start_time is not None and start_time <= datetime.now(timezone.utc):
            logger.warning("cancel_booking_past_cutoff", booking_id=str(booking.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "event has already started")

    async def _transition_and_release(self, booking: Booking) -> None:
        transitioned = await self._bookings.transition_if_confirmed(booking.id, BookingStatus.CANCELLED)
        if not transitioned:
            # Lost a race to a concurrent cancel or expiry sweep — same
            # defense-in-depth reasoning as _create_booking_row's own
            # integrity-race handling.
            logger.warning("cancel_booking_race_lost", booking_id=str(booking.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "booking is not confirmed")
        booking.status = BookingStatus.CANCELLED
        await self._hold_strategy.release_booking(booking.ticket_id)
        # Publish before commit (§22, integration point #5) — same
        # reasoning as the Phase 4 CHECKPOINT fix to handle_webhook_event:
        # publishing after commit risks stranding a CANCELLED booking whose
        # refund trigger never reached Payment Service if the publish
        # itself fails. Publishing first means a publish failure propagates
        # uncommitted and the whole request 5xx-and-retries, still CONFIRMED.
        assert self._cancelled_producer is not None, "cancel_booking requires a BookingCancelledProducer"
        await self._cancelled_producer.publish_cancelled(booking.id)
        await self._session.commit()
