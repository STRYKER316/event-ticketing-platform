import uuid

from app.kafka.consumers import ProvisioningConsumer


def make_consumer() -> ProvisioningConsumer:
    # session_factory intentionally left unset — these paths must return
    # before ever touching it, so a real value would just mask a bug.
    return ProvisioningConsumer(consumer=None, session_factory=None)


async def test_deleted_message_is_a_safe_no_op():
    consumer = make_consumer()

    await consumer._handle(f'{{"action": "deleted", "event_id": "{uuid.uuid4()}"}}'.encode())


async def test_malformed_json_does_not_raise():
    consumer = make_consumer()

    await consumer._handle(b"not json")


async def test_unknown_action_does_not_raise():
    consumer = make_consumer()

    await consumer._handle(f'{{"action": "unknown", "event_id": "{uuid.uuid4()}"}}'.encode())


async def test_valid_json_non_object_does_not_raise():
    # payload.get("action") assumes a dict; a bare list/number/string/null is
    # still valid JSON but has no .get() — this must not escape _handle and
    # kill the background consumer task.
    consumer = make_consumer()

    for non_object_payload in (b'["a", "list"]', b"5", b'"a string"', b"null"):
        await consumer._handle(non_object_payload)


async def test_invalid_upserted_payload_does_not_raise():
    # Valid JSON object, action="upserted", but missing required fields
    # (title, start_time, etc.) — model_validate rejects it, must not raise.
    consumer = make_consumer()

    await consumer._handle(f'{{"action": "upserted", "event_id": "{uuid.uuid4()}"}}'.encode())
