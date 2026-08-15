import uuid

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import EventListResponse, EventResponse, EventSortField, SeatMap, SortOrder
from app.db.event_repository import EventRepository
from app.db.seat_map_repository import SeatMapRepository


class EventManager:
    def __init__(self, session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
        self._session = session
        self._events = EventRepository(session)
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
