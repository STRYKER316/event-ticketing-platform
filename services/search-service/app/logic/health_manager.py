import structlog
from elasticsearch import AsyncElasticsearch

from app.db.health_repository import HealthRepository

logger = structlog.get_logger()


class HealthManager:
    def __init__(self, client: AsyncElasticsearch):
        self._client = client

    async def check(self) -> bool:
        try:
            await HealthRepository(self._client).ping()
        except Exception as exc:
            logger.warning("healthz_es_check_failed", error=str(exc))
            return False
        return True
