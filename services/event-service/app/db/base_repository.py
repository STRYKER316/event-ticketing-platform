import uuid
from typing import Generic, TypeVar

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Shared create/get/delete boilerplate for the simple, non-eager-loaded
    Postgres-backed repositories (Venue, Performer). EventRepository keeps its
    own get_by_id/list because it always eager-loads venue/performers."""

    def __init__(self, session: AsyncSession, model: type[ModelT]):
        self._session = session
        self._model = model

    async def create(self, instance: ModelT) -> ModelT:
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def get_by_id(self, instance_id: uuid.UUID) -> ModelT | None:
        return await self._session.get(self._model, instance_id)

    async def get_many_by_id(self, instance_ids: list[uuid.UUID]) -> list[ModelT]:
        result = await self._session.execute(select(self._model).where(self._model.id.in_(instance_ids)))
        return list(result.scalars().all())

    async def delete(self, instance_id: uuid.UUID) -> bool:
        # Core-level DELETE so rowcount is available: a concurrent duplicate delete
        # matches zero rows once the winner has already committed.
        result = await self._session.execute(sa_delete(self._model).where(self._model.id == instance_id))
        return result.rowcount > 0
