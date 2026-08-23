import uuid
from datetime import datetime, timezone

import structlog
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
    SeatMapUpsert,
    SortOrder,
)
from app.db.event_repository import EventRepository
from app.db.models import Event, EventStatus, Performer, Venue
from app.db.performer_repository import PerformerRepository
from app.db.seat_map_repository import SeatMapRepository
from app.db.venue_repository import VenueRepository
from app.kafka.producers import EventProducer

logger = structlog.get_logger()


class EventManager:
    def __init__(
        self,
        session: AsyncSession,
        mongo_db: AsyncIOMotorDatabase,
        producer: EventProducer | None = None,
    ):
        self._session = session
        self._events = EventRepository(session)
        self._venues = VenueRepository(session)
        self._performers = PerformerRepository(session)
        self._seat_maps = SeatMapRepository(mongo_db)
        # Optional: only the write paths that publish (update/publish/delete) need it — reads and create() shouldn't require a live Kafka connection.
        self._producer = producer

    async def list_events(
        self,
        limit: int,
        offset: int,
        sort_field: EventSortField,
        sort_order: SortOrder,
    ) -> EventListResponse:
        # Public listing (§15): DRAFT events are never visible here, only via a direct GET /events/{id} to their owning organizer.
        events = await self._events.list(
            limit, offset, sort_field.value, sort_order is SortOrder.DESC, status=EventStatus.PUBLISHED
        )
        total = await self._events.count(status=EventStatus.PUBLISHED)
        return EventListResponse(
            items=[EventResponse.model_validate(event) for event in events],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_event(self, event_id: uuid.UUID, user: Principal | None = None) -> EventResponse:
        event = await self._events.get_by_id(event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        self._check_visible(event, user)
        return EventResponse.model_validate(event)

    async def get_seat_map(self, event_id: uuid.UUID, user: Principal | None = None) -> SeatMap:
        event = await self._events.get_by_id(event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        self._check_visible(event, user)
        seat_map = await self._seat_maps.get_by_event_id(event_id)
        if seat_map is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "seat map not found")
        return seat_map

    async def upsert_seat_map(self, user: Principal, event_id: uuid.UUID, payload: SeatMapUpsert) -> SeatMap:
        event = await self._fetch_owned_event(user, event_id)
        self._check_seat_map_immutable_once_published(event)
        seat_map = SeatMap(event_id=event_id, sections=payload.sections)
        await self._seat_maps.upsert(seat_map)
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
        return await self.get_event(event.id, user)

    async def update_event(self, user: Principal, event_id: uuid.UUID, payload: EventUpdate) -> EventResponse:
        event = await self._fetch_owned_event(user, event_id)
        await self._apply_update(event, payload)
        await self._session.commit()
        if event.status is EventStatus.PUBLISHED:
            await self._republish(event)
        return await self.get_event(event.id, user)

    async def publish_event(self, user: Principal, event_id: uuid.UUID) -> EventResponse:
        event = await self._fetch_owned_event(user, event_id)
        if event.status is EventStatus.PUBLISHED:
            logger.warning("event_already_published", event_id=str(event_id))
            raise HTTPException(status.HTTP_409_CONFLICT, "event already published")
        if event.start_time <= datetime.now(timezone.utc):
            logger.warning("event_publish_start_time_in_past", event_id=str(event_id))
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "event start_time is no longer in the future")
        seat_map = await self._seat_maps.get_by_event_id(event_id)
        if seat_map is None:
            logger.warning("event_publish_missing_seat_map", event_id=str(event_id))
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "cannot publish an event without a seat map")
        # Publish before commit (matches payment_manager's webhook handler): a Kafka failure leaves the mutation uncommitted, so the client can retry.
        event.status = EventStatus.PUBLISHED
        await self._producer.publish_upserted(event, seat_map)
        await self._session.commit()
        return await self.get_event(event.id, user)

    async def delete_event(self, user: Principal, event_id: uuid.UUID) -> None:
        event = await self._fetch_owned_event(user, event_id)
        self._check_cannot_delete_published(event)
        # A PUBLISHED event can never reach here, so a deleted event was always DRAFT and never provisioned in Booking Service — nothing to announce.
        deleted = await self._events.delete(event_id)
        if not deleted:
            # Lost a race with a concurrent duplicate delete.
            logger.warning("event_delete_race_lost", event_id=str(event_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        await self._session.commit()
        await self._seat_maps.delete(event_id)

    async def _republish(self, event: Event, seat_map: SeatMap | None = None) -> None:
        if seat_map is None:
            seat_map = await self._seat_maps.get_by_event_id(event.id)
            if seat_map is None:
                logger.warning("published_event_missing_seat_map", event_id=str(event.id))
                return
        await self._producer.publish_upserted(event, seat_map)

    async def _fetch_owned_event(self, user: Principal, event_id: uuid.UUID) -> Event:
        event = await self._events.get_by_id(event_id)
        if event is None:
            logger.warning("event_not_found", event_id=str(event_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        if event.organizer_id != user.subject:
            if event.status is EventStatus.DRAFT:
                # DRAFT existence must stay hidden from non-owners on every route, not just GET.
                logger.warning("event_visibility_denied", event_id=str(event_id))
                raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
            logger.warning("event_ownership_check_failed", event_id=str(event_id), subject=user.subject)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not the owning organizer")
        return event

    async def _resolve_venue(self, venue_id: uuid.UUID) -> Venue:
        venue = await self._venues.get_by_id(venue_id)
        if venue is None:
            logger.warning("venue_not_found", venue_id=str(venue_id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "venue not found")
        return venue

    async def _resolve_performers(self, performer_ids: list[uuid.UUID]) -> list[Performer]:
        performers = await self._performers.get_many_by_id(performer_ids)
        if len(performers) != len(set(performer_ids)):
            logger.warning("performers_not_found", performer_ids=[str(p) for p in performer_ids])
            raise HTTPException(status.HTTP_404_NOT_FOUND, "one or more performers not found")
        return performers

    async def _apply_update(self, event: Event, payload: EventUpdate) -> None:
        if payload.title is not None:
            event.title = payload.title
        # description alone is nullable/clearable; `is not None` can't tell "omitted" from "explicit null", so it needs the model_fields_set sentinel.
        if "description" in payload.model_fields_set:
            event.description = payload.description
        if payload.start_time is not None:
            event.start_time = payload.start_time
        if payload.end_time is not None:
            event.end_time = payload.end_time
        if event.end_time <= event.start_time:
            logger.warning("event_update_invalid_time_range", event_id=str(event.id))
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "end_time must be after start_time")
        if payload.venue_id is not None:
            venue = await self._resolve_venue(payload.venue_id)
            event.venue_id = venue.id
            event.venue = venue
        if payload.performer_ids is not None:
            event.performers = await self._resolve_performers(payload.performer_ids)

    def _check_cannot_delete_published(self, event: Event) -> None:
        # Event Service can't see booking_db (§8) and there's no sixth Kafka point for a delete-time check (§7) — refuse outright once PUBLISHED.
        if event.status is EventStatus.PUBLISHED:
            logger.warning("event_delete_rejected_published", event_id=str(event.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete a published event")

    def _check_visible(self, event: Event, user: Principal | None) -> None:
        # Ownership scoping applied to visibility, not just mutation (§15); 404 (not 403) to a non-owner so DRAFT existence can't be enumerated.
        if event.status is EventStatus.DRAFT and (user is None or event.organizer_id != user.subject):
            logger.warning("event_visibility_denied", event_id=str(event.id))
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")

    def _check_seat_map_immutable_once_published(self, event: Event) -> None:
        # Once PUBLISHED, Booking Service may have already provisioned Tickets from this seat map — mutating it could silently orphan or mis-price them.
        if event.status is EventStatus.PUBLISHED:
            logger.warning("event_seat_map_mutation_rejected_published", event_id=str(event.id))
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot modify the seat map of a published event")
