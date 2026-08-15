from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.models import Venue


class VenueRepository(BaseRepository[Venue]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Venue)

    async def list(self, limit: int, offset: int) -> list[Venue]:
        result = await self._session.execute(select(Venue).order_by(Venue.name).limit(limit).offset(offset))
        return list(result.scalars().all())
