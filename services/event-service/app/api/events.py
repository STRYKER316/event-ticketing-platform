import uuid

from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from shared_auth import Principal, get_current_user_optional, require_role
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    EventCreate,
    EventListResponse,
    EventResponse,
    EventSortField,
    EventUpdate,
    SeatMap,
    SeatMapUpsert,
    SortOrder,
    VenueCreate,
    VenueResponse,
)
from app.core import get_mongo_db, get_session
from app.kafka.producers import EventProducer, get_event_producer
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
    user: Principal | None = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> EventResponse:
    """Public — no auth required. A DRAFT event is only visible to its owning
    organizer (§15); everyone else, including anonymous callers, gets 404."""
    return await EventManager(session, mongo_db).get_event(event_id, user)


@router.get("/events/{event_id}/seat-map", response_model=SeatMap)
async def get_seat_map(
    event_id: uuid.UUID,
    user: Principal | None = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> SeatMap:
    """Public — no auth required. Same DRAFT visibility scoping as GET /events/{id}."""
    return await EventManager(session, mongo_db).get_seat_map(event_id, user)


@router.put("/events/{event_id}/seat-map", response_model=SeatMap)
async def upsert_seat_map(
    event_id: uuid.UUID,
    payload: SeatMapUpsert,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> SeatMap:
    """Organizer-only, ownership-scoped: must own the event the seat map belongs to.
    Upsert semantics (§15 delta). Rejected with 409 once the event is PUBLISHED —
    Booking Service may already have provisioned Ticket rows from the current seat
    list, so mutating it in place is refused the same way delete is (organizers
    must unpublish first; not yet supported)."""
    return await EventManager(session, mongo_db).upsert_seat_map(user, event_id, payload)


@router.get("/venues/{venue_id}", response_model=VenueResponse)
async def get_venue(venue_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> VenueResponse:
    """Public — no auth required."""
    return await VenueManager(session).get_venue(venue_id)


@router.post("/venues", response_model=VenueResponse, status_code=status.HTTP_201_CREATED)
async def create_venue(
    payload: VenueCreate,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
) -> VenueResponse:
    """Organizer-only. No ownership scoping — venues are a shared catalog, not owned
    by the organizer who happens to add one (§15)."""
    return await VenueManager(session).create_venue(payload)


@router.post("/events", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    payload: EventCreate,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> EventResponse:
    """Organizer-only. Creates a DRAFT event — see POST /events/{id}/publish for the
    step that makes it visible to Search and provisions tickets (§7.2, §15 delta)."""
    return await EventManager(session, mongo_db).create_event(user, payload)


@router.patch("/events/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: uuid.UUID,
    payload: EventUpdate,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
    producer: EventProducer = Depends(get_event_producer),
) -> EventResponse:
    """Organizer-only, ownership-scoped: must own the event being updated."""
    return await EventManager(session, mongo_db, producer).update_event(user, event_id, payload)


@router.post("/events/{event_id}/publish", response_model=EventResponse)
async def publish_event(
    event_id: uuid.UUID,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
    producer: EventProducer = Depends(get_event_producer),
) -> EventResponse:
    """Organizer-only, ownership-scoped: must own the event being published.
    DRAFT -> PUBLISHED, one-way; 409 if already published, 422 if no seat map
    exists yet (§15 delta)."""
    return await EventManager(session, mongo_db, producer).publish_event(user, event_id)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: uuid.UUID,
    user: Principal = Depends(require_role("organizer")),
    session: AsyncSession = Depends(get_session),
    mongo_db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> None:
    """Organizer-only, ownership-scoped: must own the event being deleted.
    409 if the event is PUBLISHED — Booking Service may hold Ticket/Booking
    rows against it and Event Service has no channel to check (§8), so
    deletion is refused outright rather than conditionally. Deletion only
    ever touches a DRAFT event's own rows — no Kafka producer dependency
    here, so an unreachable broker can't block deleting a DRAFT event."""
    await EventManager(session, mongo_db).delete_event(user, event_id)
