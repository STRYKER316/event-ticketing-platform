import uuid
from unittest.mock import AsyncMock

from app.api.schemas import SearchSortField, SortOrder
from app.logic.search_manager import SearchManager

EVENT_ID = uuid.uuid4()

HIT = {
    "_source": {
        "event_id": str(EVENT_ID),
        "title": "Test Event",
        "description": None,
        "start_time": "2026-09-01T10:00:00Z",
        "end_time": "2026-09-01T12:00:00Z",
        "venue_name": "Test Venue",
        "performer_names": ["Test Performer"],
        "seats": [],
    }
}


def make_manager() -> tuple[SearchManager, AsyncMock]:
    manager = SearchManager(client=AsyncMock())
    manager._index = AsyncMock()
    return manager, manager._index


async def test_search_maps_hits_to_response_items():
    manager, index = make_manager()
    index.search.return_value = ([HIT], 1)

    result = await manager.search("test", limit=20, offset=0, sort_field=SearchSortField.RELEVANCE, sort_order=SortOrder.DESC)

    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].event_id == EVENT_ID
    assert result.items[0].title == "Test Event"


async def test_search_passes_through_pagination_and_sort_args():
    manager, index = make_manager()
    index.search.return_value = ([], 0)

    result = await manager.search(
        "query text", limit=5, offset=10, sort_field=SearchSortField.START_TIME, sort_order=SortOrder.ASC
    )

    index.search.assert_awaited_once_with("query text", 5, 10, "start_time", False)
    assert result.limit == 5
    assert result.offset == 10


async def test_empty_result_returns_empty_items():
    manager, index = make_manager()
    index.search.return_value = ([], 0)

    result = await manager.search("nomatch", limit=20, offset=0, sort_field=SearchSortField.RELEVANCE, sort_order=SortOrder.DESC)

    assert result.items == []
    assert result.total == 0
