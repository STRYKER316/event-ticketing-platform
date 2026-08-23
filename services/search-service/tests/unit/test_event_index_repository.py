from unittest.mock import AsyncMock

import pytest
from elastic_transport import ApiResponseMeta
from elasticsearch import BadRequestError

from app.db.event_index_repository import EVENTS_INDEX, EventIndexRepository


def make_repository() -> tuple[EventIndexRepository, AsyncMock]:
    client = AsyncMock()
    client.search.return_value = {"hits": {"hits": [], "total": {"value": 0}}}
    return EventIndexRepository(client), client


def _bad_request_error(error_type: str) -> BadRequestError:
    meta = ApiResponseMeta(status=400, http_version="1.1", headers={}, duration=0.0, node=None)
    return BadRequestError("bad request", meta, {"error": {"type": error_type}})


async def test_ensure_index_creates_when_missing():
    repository, client = make_repository()
    client.indices.exists.return_value = False
    client.cluster.health.return_value = {"timed_out": False, "status": "yellow"}

    await repository.ensure_index()

    client.indices.create.assert_awaited_once()


async def test_ensure_index_skips_create_when_already_exists():
    repository, client = make_repository()
    client.indices.exists.return_value = True
    client.cluster.health.return_value = {"timed_out": False, "status": "yellow"}

    await repository.ensure_index()

    client.indices.create.assert_not_awaited()


async def test_ensure_index_swallows_concurrent_create_race():
    # Two instances both lose the exists()-then-create() race; the loser's create() must not crash startup.
    repository, client = make_repository()
    client.indices.exists.return_value = False
    client.indices.create.side_effect = _bad_request_error("resource_already_exists_exception")
    client.cluster.health.return_value = {"timed_out": False, "status": "yellow"}

    await repository.ensure_index()  # must not raise


async def test_ensure_index_reraises_unrelated_bad_request():
    repository, client = make_repository()
    client.indices.exists.return_value = False
    client.indices.create.side_effect = _bad_request_error("mapper_parsing_exception")

    with pytest.raises(BadRequestError):
        await repository.ensure_index()


async def test_ensure_index_raises_when_health_check_times_out():
    repository, client = make_repository()
    client.indices.exists.return_value = True
    client.cluster.health.return_value = {"timed_out": True, "status": "red"}

    with pytest.raises(RuntimeError):
        await repository.ensure_index()


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
