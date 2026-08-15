from unittest.mock import AsyncMock

from app.db.event_index_repository import EVENTS_INDEX, EventIndexRepository


def make_repository() -> tuple[EventIndexRepository, AsyncMock]:
    client = AsyncMock()
    client.search.return_value = {"hits": {"hits": [], "total": {"value": 0}}}
    return EventIndexRepository(client), client


async def test_blank_query_uses_match_all():
    repository, client = make_repository()

    await repository.search("", limit=20, offset=0, sort_field="relevance", sort_desc=True)

    call = client.search.await_args
    assert call.kwargs["query"] == {"match_all": {}}
    assert call.kwargs["index"] == EVENTS_INDEX


async def test_nonblank_query_uses_multi_match_over_expected_fields():
    repository, client = make_repository()

    await repository.search("jazz", limit=20, offset=0, sort_field="relevance", sort_desc=True)

    call = client.search.await_args
    assert call.kwargs["query"] == {
        "multi_match": {"query": "jazz", "fields": ["title", "description", "venue_name", "performer_names"]}
    }


async def test_sort_always_includes_event_id_tiebreaker():
    repository, client = make_repository()

    await repository.search("jazz", limit=20, offset=0, sort_field="relevance", sort_desc=True)

    call = client.search.await_args
    assert call.kwargs["sort"][-1] == {"event_id": "asc"}


async def test_start_time_sort_field_sorts_by_start_time():
    repository, client = make_repository()

    await repository.search("", limit=20, offset=0, sort_field="start_time", sort_desc=False)

    call = client.search.await_args
    assert call.kwargs["sort"][0] == {"start_time": "asc"}


async def test_relevance_sort_field_sorts_by_score():
    repository, client = make_repository()

    await repository.search("", limit=20, offset=0, sort_field="relevance", sort_desc=True)

    call = client.search.await_args
    assert call.kwargs["sort"][0] == {"_score": "desc"}


async def test_pagination_args_pass_through():
    repository, client = make_repository()

    await repository.search("", limit=5, offset=15, sort_field="relevance", sort_desc=True)

    call = client.search.await_args
    assert call.kwargs["from_"] == 15
    assert call.kwargs["size"] == 5


async def test_returns_hits_and_total_from_response():
    repository, client = make_repository()
    client.search.return_value = {
        "hits": {"hits": [{"_source": {"title": "x"}}], "total": {"value": 42}},
    }

    hits, total = await repository.search("", limit=20, offset=0, sort_field="relevance", sort_desc=True)

    assert hits == [{"_source": {"title": "x"}}]
    assert total == 42
