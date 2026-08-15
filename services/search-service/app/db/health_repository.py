from elasticsearch import AsyncElasticsearch


class HealthRepository:
    def __init__(self, client: AsyncElasticsearch):
        self._client = client

    async def ping(self) -> None:
        await self._client.info()
