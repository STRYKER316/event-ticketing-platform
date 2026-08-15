import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Venue


class VenueRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, venue: Venue) -> Venue:
        self._session.add(venue)
        await self._session.flush()
        return venue

    async def get_by_id(self, venue_id: uuid.UUID) -> Venue | None:
        return await self._session.get(Venue, venue_id)

    async def list(self, limit: int, offset: int) -> list[Venue]:
        result = await self._session.execute(select(Venue).order_by(Venue.name).limit(limit).offset(offset))
        return list(result.scalars().all())

    async def delete(self, venue: Venue) -> None:
        await self._session.delete(venue)
        await self._session.flush()
