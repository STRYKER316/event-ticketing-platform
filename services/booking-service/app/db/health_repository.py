from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HealthRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ping(self) -> None:
        await self._session.execute(text("SELECT 1"))
