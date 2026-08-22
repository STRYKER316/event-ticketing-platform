import uuid
from datetime import datetime, timezone

import httpx
import structlog
from fastapi import HTTPException, status
from shared_auth import Principal
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import BookingPayResponse, BookingResponse, TicketStatusResponse
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
    ):
        self._session = session
        self._tickets = tickets
        self._bookings = bookings
        self._hold_strategy = hold_strategy
        self._events = events

    async def list_tickets_for_event(self, event_id: uuid.UUID) -> list[TicketStatusResponse]:
        """Public, read-only composition source for the frontend's seat map
        (§23): layout comes from Event Service, live per-seat status and
        ticket_id come from here.

        Status is never read off the raw tickets.status column, since
        RedisHoldStrategy never writes it (§6) — BOOKED is sourced from
        Booking.status=CONFIRMED instead, and HELD from the injected hold
        strategy's own is_held(), the same abstraction create_booking's own
        hold acquisition goes through."""
        tickets = await self._tickets.list_by_event(event_id)
        if not tickets:
            return []
        booked_ticket_ids = await self._bookings.list_confirmed_ticket_ids(event_id)
        responses = []
        for ticket in tickets:
            responses.append(
                TicketStatusResponse(
                    ticket_id=ticket.id,
                    section=ticket.section,
                    row_name=ticket.row_name,
                    seat_label=ticket.seat_label,
                    status=await self._resolve_ticket_status(ticket, booked_ticket_ids),
                    price_cents=ticket.price_cents,
                )
            )
        return responses

    async def _resolve_ticket_status(self, ticket: Ticket, booked_ticket_ids: set[uuid.UUID]) -> TicketStatus:
        if ticket.id in booked_ticket_ids:
            return TicketStatus.BOOKED
        if await self._hold_strategy.is_held(ticket.id):
            return TicketStatus.HELD
        return TicketStatus.AVAILABLE

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
        # Captured before any commit/rollback: session.rollback() below expires
        # every attribute on `ticket` (an ORM instance bound to this session),
        # and accessing an expired attribute triggers an implicit lazy-reload
        # that isn't safely awaitable from inside this except block — it
        # raises sqlalchemy.exc.MissingGreenlet instead, turning a clean 409
        # into an unhandled 500. Found live: a stale PENDING booking left over
        # from earlier testing (ticket.status said AVAILABLE, but an old
        # active booking row still referenced it) hit this exact branch.
        ticket_id = ticket.id
        booking = Booking(
            user_subject=user.subject, event_id=ticket.event_id, ticket_id=ticket_id, status=BookingStatus.PENDING
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
            await self._hold_strategy.release_hold(ticket_id)
            logger.warning("booking_integrity_race_lost", ticket_id=str(ticket_id))
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
        if ticket is None:
            logger.warning("pay_booking_ticket_not_found", booking_id=str(booking_id), ticket_id=str(booking.ticket_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
        return await self._charge_via_payment_service(booking, ticket, bearer_token, http_client)

    async def _fetch_owned_pending_booking(self, user: Principal, booking_id: uuid.UUID) -> Booking:
        return await self._fetch_owned_booking_in_status(
            user,
            booking_id,
            BookingStatus.PENDING,
            not_found_event="pay_booking_not_found",
            ownership_denied_event="pay_booking_ownership_denied",
            wrong_status_event="pay_booking_not_pending",
            wrong_status_detail="booking is not pending payment",
        )

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
            # Payment Service answered directly (distinct from connection/timeout below).
            # Warning, not error — mirrors payment-service's own level for this rejection.
            logger.warning(
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

    async def cancel_booking(
        self, user: Principal, booking_id: uuid.UUID, cancelled_producer: BookingCancelledProducer
    ) -> BookingResponse:
        """Owner-only, CONFIRMED-only, full-refund cancellation (§22).
        Optimistic immediate seat release — the ticket returns to AVAILABLE
        as part of this same call, not deferred to any later sweep."""
        booking = await self._fetch_owned_confirmed_booking(user, booking_id)
        await self._check_before_event_start(booking)
        await self._transition_and_release(booking, cancelled_producer)
        return self._build_response(booking)

    async def _fetch_owned_confirmed_booking(self, user: Principal, booking_id: uuid.UUID) -> Booking:
        return await self._fetch_owned_booking_in_status(
            user,
            booking_id,
            BookingStatus.CONFIRMED,
            not_found_event="cancel_booking_not_found",
            ownership_denied_event="cancel_booking_ownership_denied",
            wrong_status_event="cancel_booking_not_confirmed",
            wrong_status_detail="booking is not confirmed",
        )

    async def _fetch_owned_booking_in_status(
        self,
        user: Principal,
        booking_id: uuid.UUID,
        expected_status: BookingStatus,
        *,
        not_found_event: str,
        ownership_denied_event: str,
        wrong_status_event: str,
        wrong_status_detail: str,
    ) -> Booking:
        booking = await self._bookings.get_by_id(booking_id)
        if booking is None:
            logger.warning(not_found_event, booking_id=str(booking_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "booking not found")
        if booking.user_subject != user.subject:
            logger.warning(ownership_denied_event, booking_id=str(booking_id), subject=user.subject)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not your booking")
        if booking.status is not expected_status:
            logger.warning(wrong_status_event, booking_id=str(booking_id), status=booking.status.value)
            raise HTTPException(status.HTTP_409_CONFLICT, wrong_status_detail)
        return booking

    async def _check_before_event_start(self, booking: Booking) -> None:
        start_time = await self._events.get_start_time(booking.event_id)
        if start_time is None:
            # Fail closed, not open: a missing Event row (only reachable for
            # a booking whose event predates this table, since
            # ProvisioningConsumer writes it in the same transaction as the
            # Ticket going forward) must not silently skip the cutoff check
            # §22 amendment #2 exists to enforce.
            logger.warning("cancel_booking_no_event_start_time", booking_id=str(booking.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot verify the event's start time")
        if start_time <= datetime.now(timezone.utc):
            logger.warning("cancel_booking_past_cutoff", booking_id=str(booking.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "event has already started")

    async def _transition_and_release(
        self, booking: Booking, cancelled_producer: BookingCancelledProducer
    ) -> None:
        transitioned = await self._bookings.transition_if_confirmed(booking.id, BookingStatus.CANCELLED)
        if not transitioned:
            # Lost a race to a concurrent cancel or expiry sweep — same
            # defense-in-depth reasoning as _create_booking_row's own
            # integrity-race handling.
            logger.warning("cancel_booking_race_lost", booking_id=str(booking.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "booking is not confirmed")
        booking.status = BookingStatus.CANCELLED
        await self._hold_strategy.release_booking(booking.ticket_id)
        booking_id = booking.id
        # Commit before publish, unlike payment_manager's publish-before-commit
        # webhook ordering — that's safe only because Stripe's own redelivery
        # guarantees a retry on an uncommitted failure; a plain HTTP cancel has
        # no such external redelivery, so publishing first risked a refund
        # firing against a booking that then rolled back to CONFIRMED.
        await self._session.commit()
        try:
            await cancelled_producer.publish_cancelled(booking_id)
        except Exception:
            logger.critical("cancel_booking_publish_failed", booking_id=str(booking_id), exc_info=True)
