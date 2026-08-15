import uuid

from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from shared_auth import Principal, require_role
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    EventCreate,
    EventListResponse,
    EventResponse,
    EventSortField,
    EventUpdate,
    SeatMap,
    SortOrder,
    VenueResponse,
)
from app.core import get_mongo_db, get_session
from app.logic.event_manager import EventManager
from app.logic.venue_manager import VenueManager

router = APIRouter()


@router.get("/events", response_model=EventListResponse)
async def list_events(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_field: EventSortField = Query(default=EventSortField.START_TIME),
    sort_order: SortOrder = Query(default=SortOrder.ASC),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> EventListResponse:
    """Public — no auth required."""
    return await EventManager(session, mongo_db).list_events(limit, offset, sort_field, sort_order)


@router.get("/events/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> EventResponse:
    """Public — no auth required."""
    return await EventManager(session, mongo_db).get_event(event_id)


@router.get("/events/{event_id}/seat-map", response_model=SeatMap)
async def get_seat_map(
    event_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> SeatMap:
    """Public — no auth required."""
    return await EventManager(session, mongo_db).get_seat_map(event_id)


@router.get("/venues/{venue_id}", response_model=VenueResponse)
async def get_venue(venue_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> VenueResponse:
    """Public — no auth required."""
    return await VenueManager(session).get_venue(venue_id)


@router.post("/events", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    payload: EventCreate,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> EventResponse:
    """Organizer-only."""
    return await EventManager(session, mongo_db).create_event(user, payload)


@router.patch("/events/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: uuid.UUID,
    payload: EventUpdate,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> EventResponse:
    """Organizer-only, ownership-scoped: must own the event being updated."""
    return await EventManager(session, mongo_db).update_event(user, event_id, payload)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: uuid.UUID,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> None:
    """Organizer-only, ownership-scoped: must own the event being deleted."""
    await EventManager(session, mongo_db).delete_event(user, event_id)
