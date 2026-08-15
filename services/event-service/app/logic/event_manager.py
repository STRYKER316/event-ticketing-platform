import uuid

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from shared_auth import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    EventCreate,
    EventListResponse,
    EventResponse,
    EventSortField,
    EventUpdate,
    SeatMap,
    SortOrder,
)
from app.db.event_repository import EventRepository
from app.db.models import Event, EventStatus, Performer, Venue
from app.db.performer_repository import PerformerRepository
from app.db.seat_map_repository import SeatMapRepository
from app.db.venue_repository import VenueRepository


class EventManager:
    def __init__(self, session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
        self._session = session
        self._events = EventRepository(session)
        self._venues = VenueRepository(session)
        self._performers = PerformerRepository(session)
        self._seat_maps = SeatMapRepository(mongo_db)

    async def list_events(
        self,
        limit: int,
        offset: int,
        sort_field: EventSortField,
        sort_order: SortOrder,
    ) -> EventListResponse:
        events = await self._events.list(limit, offset, sort_field.value, sort_order is SortOrder.DESC)
        total = await self._events.count()
        return EventListResponse(
            items=[EventResponse.model_validate(event) for event in events],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_event(self, event_id: uuid.UUID) -> EventResponse:
        event = await self._events.get_by_id(event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        return EventResponse.model_validate(event)

    async def get_seat_map(self, event_id: uuid.UUID) -> SeatMap:
        event = await self._events.get_by_id(event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        seat_map = await self._seat_maps.get_by_event_id(str(event_id))
        if seat_map is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "seat map not found")
        return seat_map

    async def create_event(self, user: Principal, payload: EventCreate) -> EventResponse:
        venue = await self._resolve_venue(payload.venue_id)
        performers = await self._resolve_performers(payload.performer_ids)
        event = Event(
            title=payload.title,
            description=payload.description,
            start_time=payload.start_time,
            end_time=payload.end_time,
            status=EventStatus.DRAFT,
            organizer_id=user.subject,
            venue_id=venue.id,
        )
        event.performers = performers
        await self._events.create(event)
        await self._session.commit()
        return await self.get_event(event.id)

    async def update_event(self, user: Principal, event_id: uuid.UUID, payload: EventUpdate) -> EventResponse:
        event = await self._fetch_owned_event(user, event_id)
        await self._apply_update(event, payload)
        await self._session.commit()
        return await self.get_event(event.id)

    async def delete_event(self, user: Principal, event_id: uuid.UUID) -> None:
        event = await self._fetch_owned_event(user, event_id)
        self._check_no_bookings(event)
        await self._events.delete(event)
        await self._session.commit()

    async def _fetch_owned_event(self, user: Principal, event_id: uuid.UUID) -> Event:
        event = await self._events.get_by_id(event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        if event.organizer_id != user.subject:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not the owning organizer")
        return event

    async def _resolve_venue(self, venue_id: uuid.UUID) -> Venue:
        venue = await self._venues.get_by_id(venue_id)
        if venue is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "venue not found")
        return venue

    async def _resolve_performers(self, performer_ids: list[uuid.UUID]) -> list[Performer]:
        performers = await self._performers.get_many_by_id(performer_ids)
        if len(performers) != len(set(performer_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "one or more performers not found")
        return performers

    async def _apply_update(self, event: Event, payload: EventUpdate) -> None:
        if payload.title is not None:
            event.title = payload.title
        if payload.description is not None:
            event.description = payload.description
        if payload.start_time is not None:
            event.start_time = payload.start_time
        if payload.end_time is not None:
            event.end_time = payload.end_time
        if event.end_time <= event.start_time:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end_time must be after start_time")
        if payload.venue_id is not None:
            venue = await self._resolve_venue(payload.venue_id)
            event.venue_id = venue.id
        if payload.performer_ids is not None:
            event.performers = await self._resolve_performers(payload.performer_ids)

    def _check_no_bookings(self, event: Event) -> None:
        return
