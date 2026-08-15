import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Event


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

    async def list(self, limit: int, offset: int) -> list[Event]:
        result = await self._session.execute(
            select(Event)
            .options(selectinload(Event.venue), selectinload(Event.performers))
            .order_by(Event.start_time)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def delete(self, event: Event) -> None:
        await self._session.delete(event)
        await self._session.flush()
