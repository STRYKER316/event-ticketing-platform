from typing import Any

from elasticsearch import AsyncElasticsearch, NotFoundError

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
            # number_of_replicas=0: the local/demo topology is single-node ES
            # (§12, §24) — a replica could never be assigned to a second node,
            # so it would sit unassigned forever and keep cluster health at
            # "yellow" for no reason.
            await self._client.indices.create(
                index=EVENTS_INDEX,
                mappings={"properties": EVENTS_INDEX_MAPPING},
                settings={"number_of_replicas": 0},
            )
        # Block until the index's shards are actually assigned, so a caller
        # (service startup, a test) never observes a healthy-looking index
        # that isn't queryable/writable yet.
        await self._client.cluster.health(index=EVENTS_INDEX, wait_for_status="yellow", timeout="30s")

    async def upsert(self, event_id: str, document: dict[str, Any]) -> None:
        # Indexing by the event ID (§7 idempotency) means a redelivered upsert
        # overwrites the same document rather than creating a duplicate.
        await self._client.index(index=EVENTS_INDEX, id=event_id, document=document)

    async def delete(self, event_id: str) -> None:
        try:
            await self._client.delete(index=EVENTS_INDEX, id=event_id)
        except NotFoundError:
            # Already gone — a redelivered or out-of-order delete is a safe no-op.
            pass

    async def search(
        self, query: str, limit: int, offset: int, sort_field: str, sort_desc: bool
    ) -> tuple[list[dict[str, Any]], int]:
        es_query: dict[str, Any] = (
            {"multi_match": {"query": query, "fields": ["title", "description", "venue_name", "performer_names"]}}
            if query
            else {"match_all": {}}
        )
        order = "desc" if sort_desc else "asc"
        # event_id tiebreaker: a single-field sort (relevance or start_time
        # alone) can tie across rows, which duplicates/drops results across
        # pages — same pagination-stability lesson as event-service's list().
        primary_sort = {"start_time": order} if sort_field == "start_time" else {"_score": order}
        response = await self._client.search(
            index=EVENTS_INDEX,
            query=es_query,
            sort=[primary_sort, {"event_id": "asc"}],
            from_=offset,
            size=limit,
            track_total_hits=True,
        )
        hits = response["hits"]["hits"]
        total = response["hits"]["total"]["value"]
        return hits, total
