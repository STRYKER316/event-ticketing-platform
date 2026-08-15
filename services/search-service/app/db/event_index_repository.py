from elasticsearch import AsyncElasticsearch

EVENTS_INDEX = "events"

# Mirrors the event-carried Kafka payload (event-service's EventUpsertedMessage,
# §7.2) — this index is populated exclusively from that message, never queried
# back into event-service (§8, Elasticsearch is not a source of truth).
EVENTS_INDEX_MAPPING = {
    "event_id": {"type": "keyword"},
    "title": {"type": "text"},
    "description": {"type": "text"},
    "start_time": {"type": "date"},
    "end_time": {"type": "date"},
    "venue_name": {"type": "text"},
    "performer_names": {"type": "text"},
    "seats": {
        "type": "nested",
        "properties": {
            "section": {"type": "keyword"},
            "row": {"type": "keyword"},
            "label": {"type": "keyword"},
        },
    },
}


class EventIndexRepository:
    def __init__(self, client: AsyncElasticsearch):
        self._client = client

    async def ensure_index(self) -> None:
        if not await self._client.indices.exists(index=EVENTS_INDEX):
            await self._client.indices.create(index=EVENTS_INDEX, mappings={"properties": EVENTS_INDEX_MAPPING})
