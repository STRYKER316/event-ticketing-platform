import uuid
from typing import Generic, TypeVar

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.chunking import chunked

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Shared create/get/delete boilerplate for simple, non-eager-loaded
    repositories."""

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
        # Batched: Postgres/asyncpg caps a single statement at ~32,767 bind params.
        instances: list[ModelT] = []
        for batch in chunked(instance_ids):
            result = await self._session.execute(select(self._model).where(self._model.id.in_(batch)))
            instances.extend(result.scalars().all())
        return instances

    async def delete(self, instance_id: uuid.UUID) -> bool:
        # Core-level DELETE so rowcount is available: a concurrent duplicate delete
        # matches zero rows once the winner has already committed.
        result = await self._session.execute(sa_delete(self._model).where(self._model.id == instance_id))
        return result.rowcount > 0
