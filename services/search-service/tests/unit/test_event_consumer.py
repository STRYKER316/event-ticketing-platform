import json
import uuid
from unittest.mock import AsyncMock

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
    "seats": [{"section": "A", "row": "1", "label": "A1"}],
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
    assert document_arg["seats"] == [{"section": "A", "row": "1", "label": "A1"}]
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


async def test_repository_failure_is_caught_not_raised():
    consumer, repository = make_consumer()
    repository.upsert.side_effect = RuntimeError("elasticsearch down")

    await consumer._handle(json.dumps(UPSERT_PAYLOAD).encode())  # must not raise
