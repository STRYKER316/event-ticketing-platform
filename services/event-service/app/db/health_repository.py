from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HealthRepository:
    def __init__(self, session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
        self._session = session
        self._mongo_db = mongo_db

    async def ping(self) -> None:
        await self._session.execute(text("SELECT 1"))
        await self._mongo_db.command("ping")
