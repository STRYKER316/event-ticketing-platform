import uuid

import httpx
from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from shared_auth import Principal, get_current_user
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import BookingCreate, BookingPayResponse, BookingResponse
from app.core import get_http_client, get_redis, get_session
from app.db.booking_repository import BookingRepository
from app.db.ticket_repository import TicketRepository
from app.logic.booking_manager import BookingManager
from app.logic.helpers.hold_strategy_factory import get_hold_strategy

router = APIRouter()
# Separate from shared_auth's own internal bearer-scheme instance — this one
# exists purely to recover the raw token string so it can be forwarded to
# Payment Service unmodified (§9 amendment); it parses the same
# Authorization header get_current_user already validates, no extra cost.
_bearer_scheme = HTTPBearer(auto_error=True)


@router.post("/bookings", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    payload: BookingCreate,
    user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> BookingResponse:
    """Authenticated-only — any logged-in user may book a ticket, no
    organizer role required (§15 delta: booking is not an organizer
    action)."""
    manager = BookingManager(
        session=session,
        tickets=TicketRepository(session),
        bookings=BookingRepository(session),
        hold_strategy=get_hold_strategy(session, redis),
    )
    return await manager.create_booking(user, payload.ticket_id)


@router.post("/bookings/{booking_id}/pay", response_model=BookingPayResponse)
async def pay_booking(
    booking_id: uuid.UUID,
    user: Principal = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> BookingPayResponse:
    """Authenticated, ownership-scoped: only the booking's own user may pay
    for it (403 otherwise), and only while it's still PENDING (409
    otherwise). Fronts payment for Payment Service, which has no access to
    booking_db to check either of those itself (decisions-log §9
    amendment)."""
    manager = BookingManager(
        session=session,
        tickets=TicketRepository(session),
        bookings=BookingRepository(session),
        hold_strategy=get_hold_strategy(session, redis),
    )
    return await manager.pay_booking(user, booking_id, credentials.credentials, http_client)
