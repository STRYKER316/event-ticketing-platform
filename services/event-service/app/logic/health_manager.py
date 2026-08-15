import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.health_repository import HealthRepository

logger = structlog.get_logger()


class HealthManager:
    def __init__(self, session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
        self._session = session
        self._mongo_db = mongo_db

    async def check(self) -> bool:
        try:
            await HealthRepository(self._session, self._mongo_db).ping()
        except Exception as exc:
            logger.warning("healthz_db_check_failed", error=str(exc))
            return False
        return True
