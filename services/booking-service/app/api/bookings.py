from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis
from shared_auth import Principal, get_current_user
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import BookingCreate, BookingResponse
from app.core import get_redis, get_session
from app.db.booking_repository import BookingRepository
from app.db.ticket_repository import TicketRepository
from app.logic.booking_manager import BookingManager
from app.logic.helpers.hold_strategy_factory import get_hold_strategy

router = APIRouter()


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
