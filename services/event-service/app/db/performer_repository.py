import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Performer


class PerformerRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, performer: Performer) -> Performer:
        self._session.add(performer)
        await self._session.flush()
        return performer

    async def get_by_id(self, performer_id: uuid.UUID) -> Performer | None:
        return await self._session.get(Performer, performer_id)

    async def get_many_by_id(self, performer_ids: list[uuid.UUID]) -> list[Performer]:
        result = await self._session.execute(select(Performer).where(Performer.id.in_(performer_ids)))
        return list(result.scalars().all())

    async def delete(self, performer: Performer) -> None:
        await self._session.delete(performer)
        await self._session.flush()
