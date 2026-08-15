import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Event

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

    async def list(self, limit: int, offset: int, sort_field: str, sort_desc: bool) -> list[Event]:
        column = _SORT_COLUMNS[sort_field]
        order = column.desc() if sort_desc else column.asc()
        tiebreaker = Event.id.desc() if sort_desc else Event.id.asc()
        result = await self._session.execute(
            select(Event)
            .options(selectinload(Event.venue), selectinload(Event.performers))
            .order_by(order, tiebreaker)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Event))
        return result.scalar_one()

    async def delete(self, event: Event) -> None:
        await self._session.delete(event)
        await self._session.flush()
