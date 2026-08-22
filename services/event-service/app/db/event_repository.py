import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Event, EventStatus

_SORT_COLUMNS = {"start_time": Event.start_time, "title": Event.title}


class EventRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, event: Event) -> Event:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_by_id(self, event_id: uuid.UUID) -> Event | None:
        result = await self._session.execute(
            select(Event)
            .options(selectinload(Event.venue), selectinload(Event.performers))
            .where(Event.id == event_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        limit: int,
        offset: int,
        sort_field: str,
        sort_desc: bool,
        status: EventStatus | None = None,
    ) -> list[Event]:
        # status=None (the default) is unfiltered — used by callers with their own
        # visibility rules (e.g. seed.py's "any data already present" check). The
        # public listing route filters to PUBLISHED explicitly (EventManager.list_events).
        column = _SORT_COLUMNS[sort_field]
        order = column.desc() if sort_desc else column.asc()
        tiebreaker = Event.id.desc() if sort_desc else Event.id.asc()
        stmt = select(Event).options(selectinload(Event.venue), selectinload(Event.performers))
        if status is not None:
            stmt = stmt.where(Event.status == status)
        stmt = stmt.order_by(order, tiebreaker).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, status: EventStatus | None = None) -> int:
        stmt = select(func.count()).select_from(Event)
        if status is not None:
            stmt = stmt.where(Event.status == status)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def delete(self, event_id: uuid.UUID) -> bool:
        # Core-level DELETE so rowcount is available: a concurrent duplicate delete
        # matches zero rows once the winner has already committed.
        result = await self._session.execute(sa_delete(Event).where(Event.id == event_id))
        return result.rowcount > 0
