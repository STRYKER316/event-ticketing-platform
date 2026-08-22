from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Shared create boilerplate for simple, non-eager-loaded repositories."""

    def __init__(self, session: AsyncSession, model: type[ModelT]):
        self._session = session
        self._model = model

    async def create(self, instance: ModelT) -> ModelT:
        self._session.add(instance)
        await self._session.flush()
        return instance
