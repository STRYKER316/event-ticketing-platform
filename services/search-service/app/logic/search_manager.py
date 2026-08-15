from typing import Any

from elasticsearch import AsyncElasticsearch

from app.api.schemas import SearchResponse, SearchResultItem, SearchSortField, SortOrder
from app.db.event_index_repository import EventIndexRepository


class SearchManager:
    def __init__(self, client: AsyncElasticsearch):
        self._index = EventIndexRepository(client)

    async def search(
        self, query: str, limit: int, offset: int, sort_field: SearchSortField, sort_order: SortOrder
    ) -> SearchResponse:
        hits, total = await self._index.search(query, limit, offset, sort_field.value, sort_order is SortOrder.DESC)
        return SearchResponse(
            items=[self._to_item(hit) for hit in hits],
            total=total,
            limit=limit,
            offset=offset,
        )

    def _to_item(self, hit: dict[str, Any]) -> SearchResultItem:
        source = hit["_source"]
        return SearchResultItem(
            event_id=source["event_id"],
            title=source["title"],
            description=source["description"],
            start_time=source["start_time"],
            end_time=source["end_time"],
            venue_name=source["venue_name"],
            performer_names=source["performer_names"],
        )
