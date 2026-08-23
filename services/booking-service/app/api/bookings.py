import uuid

import httpx
from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from shared_auth import Principal, get_current_user
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import BookingCreate, BookingPayResponse, BookingResponse, TicketStatusResponse
from app.core import get_http_client, get_redis, get_session
from app.db.booking_repository import BookingRepository
from app.db.event_repository import EventRepository
from app.db.ticket_repository import TicketRepository
from app.kafka.producers import BookingCancelledProducer, get_booking_cancelled_producer
from app.logic.booking_manager import BookingManager
from app.logic.helpers.hold_strategy_factory import get_hold_strategy

router = APIRouter()
# Separate from shared_auth's own bearer-scheme instance — this one just recovers the raw token to forward to Payment Service unmodified (§9 amendment).
_bearer_scheme = HTTPBearer(auto_error=True)


def get_booking_manager(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> BookingManager:
    """Shared construction point — every route builds its BookingManager
    here instead of inline, so one edit reaches all of them."""
    return BookingManager(
        session=session,
        tickets=TicketRepository(session),
        bookings=BookingRepository(session),
        hold_strategy=get_hold_strategy(session, redis),
        events=EventRepository(session),
    )


@router.get("/bookings/events/{event_id}/tickets", response_model=list[TicketStatusResponse])
async def list_tickets_for_event(
    event_id: uuid.UUID,
    manager: BookingManager = Depends(get_booking_manager),
) -> list[TicketStatusResponse]:
    """Public — no auth required. Read-only composition source for the
    frontend's seat map (§23): layout comes from Event Service, live
    per-seat status and ticket_id come from here."""
    return await manager.list_tickets_for_event(event_id)


@router.post("/bookings", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    payload: BookingCreate,
    user: Principal = Depends(get_current_user),
    manager: BookingManager = Depends(get_booking_manager),
) -> BookingResponse:
    """Authenticated-only — any logged-in user may book a ticket, no
    organizer role required (§15 delta: booking is not an organizer
    action)."""
    return await manager.create_booking(user, payload.ticket_id)


@router.post("/bookings/{booking_id}/pay", response_model=BookingPayResponse)
async def pay_booking(
    booking_id: uuid.UUID,
    user: Principal = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    manager: BookingManager = Depends(get_booking_manager),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> BookingPayResponse:
    """Authenticated, ownership-scoped: only the booking's own user may pay
    for it (403 otherwise), and only while it's still PENDING (409
    otherwise). Fronts payment for Payment Service, which has no access to
    booking_db to check either of those itself (decisions-log §9
    amendment)."""
    return await manager.pay_booking(user, booking_id, credentials.credentials, http_client)


@router.post("/bookings/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(
    booking_id: uuid.UUID,
    user: Principal = Depends(get_current_user),
    manager: BookingManager = Depends(get_booking_manager),
    cancelled_producer: BookingCancelledProducer = Depends(get_booking_cancelled_producer),
) -> BookingResponse:
    """Authenticated, ownership-scoped: only the booking's own user may
    cancel it (403 otherwise), and only while it's still CONFIRMED (409
    otherwise) and before the event's start time (409 otherwise, §22)."""
    return await manager.cancel_booking(user, booking_id, cancelled_producer)
