import uuid
from datetime import datetime, timedelta, timezone

from app.kafka import consumers
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


def _upserted_payload(event_id: uuid.UUID) -> bytes:
    start = datetime.now(timezone.utc) + timedelta(days=1)
    payload = (
        f'{{"action": "upserted", "event_id": "{event_id}", "title": "T", '
        f'"description": null, "start_time": "{start.isoformat()}", '
        f'"end_time": "{(start + timedelta(hours=2)).isoformat()}", '
        f'"venue_name": "V", "performer_names": [], '
        f'"seats": [{{"section": "A", "row": "1", "label": "A1", "price_cents": 2500}}]}}'
    )
    return payload.encode()


async def test_db_write_failure_does_not_raise_and_retries_every_attempt(monkeypatch):
    # A well-formed message whose DB write blows up on every attempt
    # (connection dropped, constraint violation, whatever) must not escape
    # _handle and kill the background consumer task — see _write_tickets()
    # in consumers.py. session_factory() raising is the simplest way to
    # force that path without a real broken database. Zero backoff keeps
    # this test fast; call-count proves every attempt actually happened
    # rather than giving up after the first.
    monkeypatch.setattr(consumers, "DB_WRITE_RETRY_BACKOFF_SECONDS", 0)
    call_count = 0

    def session_factory():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("session factory boom")

    consumer = ProvisioningConsumer(consumer=None, session_factory=session_factory)

    await consumer._handle(_upserted_payload(uuid.uuid4()))

    assert call_count == consumers.DB_WRITE_MAX_ATTEMPTS
