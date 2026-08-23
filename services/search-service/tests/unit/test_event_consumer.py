import json
import uuid
from unittest.mock import AsyncMock

from app.kafka import consumers
from app.kafka.consumers import EventConsumer

EVENT_ID = uuid.uuid4()

UPSERT_PAYLOAD = {
    "action": "upserted",
    "event_id": str(EVENT_ID),
    "title": "Test Event",
    "description": None,
    "start_time": "2026-09-01T10:00:00Z",
    "end_time": "2026-09-01T12:00:00Z",
    "venue_name": "Test Venue",
    "performer_names": ["Test Performer"],
    "seats": [{"section": "A", "row": "1", "label": "A1", "price_cents": 2500}],
}

DELETE_PAYLOAD = {"action": "deleted", "event_id": str(EVENT_ID)}


def make_consumer() -> tuple[EventConsumer, AsyncMock]:
    repository = AsyncMock()
    consumer = EventConsumer(consumer=AsyncMock(), repository=repository)
    return consumer, repository


async def test_upsert_message_calls_repository_upsert():
    consumer, repository = make_consumer()

    await consumer._handle(json.dumps(UPSERT_PAYLOAD).encode())

    repository.upsert.assert_awaited_once()
    event_id_arg, document_arg = repository.upsert.await_args.args
    assert event_id_arg == str(EVENT_ID)
    assert document_arg["title"] == "Test Event"
    assert document_arg["seats"] == [{"section": "A", "row": "1", "label": "A1", "price_cents": 2500}]
    repository.delete.assert_not_awaited()


async def test_delete_message_calls_repository_delete():
    consumer, repository = make_consumer()

    await consumer._handle(json.dumps(DELETE_PAYLOAD).encode())

    repository.delete.assert_awaited_once_with(str(EVENT_ID))
    repository.upsert.assert_not_awaited()


async def test_malformed_json_does_not_raise():
    consumer, repository = make_consumer()

    await consumer._handle(b"not json")

    repository.upsert.assert_not_awaited()
    repository.delete.assert_not_awaited()


async def test_unknown_action_does_not_raise():
    consumer, repository = make_consumer()

    await consumer._handle(json.dumps({"action": "unknown", "event_id": str(EVENT_ID)}).encode())

    repository.upsert.assert_not_awaited()
    repository.delete.assert_not_awaited()


async def test_repository_failure_is_caught_not_raised_after_exhausting_retries(monkeypatch):
    # Zero backoff keeps this fast; call-count proves every attempt happened, not just the first — same pattern as booking-service's equivalent test.
    monkeypatch.setattr(consumers, "ES_WRITE_RETRY_BACKOFF_SECONDS", 0)
    consumer, repository = make_consumer()
    repository.upsert.side_effect = RuntimeError("elasticsearch down")

    await consumer._handle(json.dumps(UPSERT_PAYLOAD).encode())  # must not raise

    assert repository.upsert.await_count == consumers.ES_WRITE_MAX_ATTEMPTS


async def test_transient_repository_failure_is_retried_then_succeeds(monkeypatch):
    # Regression test: a transient failure on early attempts must not be treated as permanent loss — the write retries and still succeeds once it clears.
    monkeypatch.setattr(consumers, "ES_WRITE_RETRY_BACKOFF_SECONDS", 0)
    consumer, repository = make_consumer()
    repository.upsert.side_effect = [RuntimeError("transient ES blip"), None]

    await consumer._handle(json.dumps(UPSERT_PAYLOAD).encode())

    assert repository.upsert.await_count == 2


async def test_offset_only_committed_after_handle_finishes():
    # run()'s per-record offset commit must happen strictly after _handle() returns, not on aiokafka's background auto-commit timer.
    calls: list[str] = []
    repository = AsyncMock()
    repository.upsert.side_effect = lambda *_a, **_kw: calls.append("write")

    class _Record:
        value = json.dumps(UPSERT_PAYLOAD).encode()

    kafka_consumer = AsyncMock()
    kafka_consumer.__aiter__.return_value = [_Record()]
    kafka_consumer.commit.side_effect = lambda: calls.append("commit")

    consumer = EventConsumer(consumer=kafka_consumer, repository=repository)

    await consumer.run()

    assert calls == ["write", "commit"]


async def test_valid_json_non_object_does_not_raise():
    # payload.get("action") assumes a dict; a bare list/number/string/null is valid JSON but has no .get() — this must not escape _handle and kill the consumer task.
    consumer, repository = make_consumer()

    for non_object_payload in (["a", "list"], 5, "a string", None):
        await consumer._handle(json.dumps(non_object_payload).encode())

    repository.upsert.assert_not_awaited()
    repository.delete.assert_not_awaited()
