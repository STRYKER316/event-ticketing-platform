import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.health_repository import HealthRepository

logger = structlog.get_logger()


class HealthManager:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check(self) -> bool:
        try:
            await HealthRepository(self._session).ping()
        except Exception:
            logger.warning("healthz_db_check_failed")
            return False
        return True
