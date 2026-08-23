from typing import Any

from elasticsearch import AsyncElasticsearch, BadRequestError, NotFoundError

EVENTS_INDEX = "events"

# elasticsearch-py raises BadRequestError (400) for a lost concurrent-create race — the type is in the body, not a dedicated exception class.
_RESOURCE_ALREADY_EXISTS_ERROR_TYPE = "resource_already_exists_exception"

# Mirrors event-service's EventUpsertedMessage payload (§7.2) — this index is populated only from Kafka, never queried back into event-service (§8).
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
            # number_of_replicas=0: single-node ES topology (§12, §24) — a replica could never be assigned, leaving health stuck "yellow" for no reason.
            try:
                await self._client.indices.create(
                    index=EVENTS_INDEX,
                    mappings={"properties": EVENTS_INDEX_MAPPING},
                    settings={"number_of_replicas": 0},
                )
            except BadRequestError as exc:
                # Check-then-act race: another instance's create won first — index existing is a success, not a crash; other 400s still propagate.
                body = exc.body if isinstance(exc.body, dict) else {}
                error_type = body.get("error", {}).get("type") if isinstance(body.get("error"), dict) else None
                if error_type != _RESOURCE_ALREADY_EXISTS_ERROR_TYPE:
                    raise
        # Blocks until shards are assigned so callers never see a healthy-looking but unqueryable index; cluster.health() sets timed_out=True instead of raising, so it must be checked explicitly.
        health = await self._client.cluster.health(index=EVENTS_INDEX, wait_for_status="yellow", timeout="30s")
        if health.get("timed_out"):
            raise RuntimeError(
                f"Elasticsearch index '{EVENTS_INDEX}' did not reach 'yellow' status "
                f"within the wait timeout (status={health.get('status')})"
            )

    async def upsert(self, event_id: str, document: dict[str, Any]) -> None:
        # Indexing by event ID (§7 idempotency): a redelivered upsert overwrites the same document instead of duplicating.
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
        # event_id tiebreaker: a single-field sort alone can tie across rows, duplicating/dropping results across pages — same lesson as event-service's list().
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
