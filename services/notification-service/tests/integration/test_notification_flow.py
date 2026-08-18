import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator

import pytest
import structlog.testing
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.kafka.consumers import DlqConsumer, NotificationConsumer, RetryConsumer
from app.kafka.producers import RetryPublisher
from app.kafka.schemas import RetryEnvelope

from .conftest import NOTIFICATION_DLQ_TOPIC, NOTIFICATION_RETRY_TOPIC, NOTIFICATIONS_TOPIC, make_topic_consumer

pytestmark = pytest.mark.asyncio


async def _wait_until(predicate, timeout: float = 15, interval: float = 0.2) -> None:
    async def poll():
        while not await predicate():
            await asyncio.sleep(interval)

    await asyncio.wait_for(poll(), timeout=timeout)


def _notification_message(action: str, booking_id: str) -> bytes:
    return json.dumps({"action": action, "booking_id": booking_id, "reason": None}).encode()


async def _find_matching_record(consumer: AIOKafkaConsumer, booking_id: str, timeout: float = 10):
    """Every producer in this system keys by booking ID, so filtering on the
    record key isolates this test's own message from another test's
    leftovers on the same session-scoped topic (both consumers use a fresh,
    unique group id with auto_offset_reset='earliest', so they always see
    the whole topic history, not just what this test itself produced)."""

    async def _scan():
        while True:
            record = await consumer.getone()
            if record.key and record.key.decode() == booking_id:
                return record

    return await asyncio.wait_for(_scan(), timeout=timeout)


async def _assert_no_matching_record(consumer: AIOKafkaConsumer, booking_id: str, timeout: float = 5) -> None:
    with pytest.raises(TimeoutError):
        await _find_matching_record(consumer, booking_id, timeout=timeout)


@pytest.fixture
async def retry_publisher(kafka_container, kafka_producer: AIOKafkaProducer) -> RetryPublisher:
    return RetryPublisher(kafka_producer, NOTIFICATION_RETRY_TOPIC, NOTIFICATION_DLQ_TOPIC)


@pytest.fixture
async def running_notification_consumer(
    kafka_container, retry_publisher: RetryPublisher
) -> AsyncIterator[None]:
    consumer = AIOKafkaConsumer(
        NOTIFICATIONS_TOPIC,
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        group_id=f"notification-service-test-{uuid.uuid4()}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    task = asyncio.create_task(NotificationConsumer(consumer, retry_publisher).run())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await consumer.stop()


@pytest.fixture
async def running_retry_consumer(kafka_container, retry_publisher: RetryPublisher) -> AsyncIterator[None]:
    consumer = AIOKafkaConsumer(
        NOTIFICATION_RETRY_TOPIC,
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        group_id=f"notification-service-test-{uuid.uuid4()}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    task = asyncio.create_task(RetryConsumer(consumer, retry_publisher).run())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await consumer.stop()


async def test_successful_delivery_produces_no_retry_message(
    kafka_container, kafka_producer: AIOKafkaProducer, fast_retry_settings, running_notification_consumer
):
    booking_id = str(uuid.uuid4())
    retry_consumer = await make_topic_consumer(kafka_container, NOTIFICATION_RETRY_TOPIC)
    try:
        await kafka_producer.send_and_wait(
            NOTIFICATIONS_TOPIC, key=booking_id.encode(), value=_notification_message("booking_confirmed", booking_id)
        )
        # No forced failure this test (simulated_failure_attempts=0) — assert
        # nothing lands on notification-retry keyed to this booking (absence,
        # not just a positive assertion).
        await _assert_no_matching_record(retry_consumer, booking_id, timeout=3)
    finally:
        await retry_consumer.stop()


async def test_forced_failure_produces_retry_envelope_with_attempt_two(
    kafka_container, kafka_producer: AIOKafkaProducer, fast_retry_settings, running_notification_consumer
):
    fast_retry_settings.simulated_failure_attempts = 1  # fails attempt 1 only
    booking_id = str(uuid.uuid4())
    retry_consumer = await make_topic_consumer(kafka_container, NOTIFICATION_RETRY_TOPIC)
    try:
        await kafka_producer.send_and_wait(
            NOTIFICATIONS_TOPIC, key=booking_id.encode(), value=_notification_message("payment_confirmed", booking_id)
        )
        record = await _find_matching_record(retry_consumer, booking_id)
        envelope = RetryEnvelope.model_validate_json(record.value)
        assert envelope.attempt == 2
        assert envelope.original.booking_id == uuid.UUID(booking_id)
        assert envelope.last_error
    finally:
        await retry_consumer.stop()


async def test_retry_consumer_recovers_and_does_not_republish_to_dlq(
    kafka_container,
    kafka_producer: AIOKafkaProducer,
    fast_retry_settings,
    running_notification_consumer,
    running_retry_consumer,
):
    fast_retry_settings.simulated_failure_attempts = 1  # attempt 1 fails, attempt 2 (the retry) succeeds
    booking_id = str(uuid.uuid4())
    dlq_consumer = await make_topic_consumer(kafka_container, NOTIFICATION_DLQ_TOPIC)
    try:
        await kafka_producer.send_and_wait(
            NOTIFICATIONS_TOPIC, key=booking_id.encode(), value=_notification_message("refund_failed", booking_id)
        )
        # The retry succeeds on its own (RetryConsumer's real backoff sleep,
        # kept short by fast_retry_settings) — nothing should ever reach the DLQ.
        await _assert_no_matching_record(dlq_consumer, booking_id, timeout=6)
    finally:
        await dlq_consumer.stop()


async def test_exhausted_retries_lands_in_dlq(
    kafka_container,
    kafka_producer: AIOKafkaProducer,
    fast_retry_settings,
    running_notification_consumer,
    running_retry_consumer,
):
    # retry_max_attempts=1 (fast_retry_settings) — attempt 2 (the one retry)
    # also fails, so it must route to the DLQ rather than retry forever.
    fast_retry_settings.simulated_failure_attempts = 99
    booking_id = str(uuid.uuid4())
    dlq_consumer = await make_topic_consumer(kafka_container, NOTIFICATION_DLQ_TOPIC)
    try:
        await kafka_producer.send_and_wait(
            NOTIFICATIONS_TOPIC, key=booking_id.encode(), value=_notification_message("booking_confirmed", booking_id)
        )
        record = await _find_matching_record(dlq_consumer, booking_id)
        envelope = RetryEnvelope.model_validate_json(record.value)
        assert envelope.original.booking_id == uuid.UUID(booking_id)
        assert envelope.attempt == 2  # exhausted at the one retry fast_retry_settings allows
        assert envelope.last_error
    finally:
        await dlq_consumer.stop()


async def test_dlq_consumer_logs_receipt(kafka_container, kafka_producer: AIOKafkaProducer):
    booking_id = str(uuid.uuid4())
    envelope = RetryEnvelope.model_validate(
        {
            "attempt": 4,
            "original": {"action": "refund_failed", "booking_id": booking_id, "reason": "card declined"},
            "last_error": "simulated failure for test_dlq_consumer_logs_receipt",
        }
    )

    consumer = AIOKafkaConsumer(
        NOTIFICATION_DLQ_TOPIC,
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        group_id=f"notification-service-test-{uuid.uuid4()}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    task = asyncio.create_task(DlqConsumer(consumer).run())
    try:
        # structlog isn't wired through stdlib logging in the test process
        # (configure_logging() only ever runs from main.py's create_app()),
        # so structlog's own capture_logs() is used instead of pytest's
        # stdlib-logging-based caplog fixture.
        with structlog.testing.capture_logs() as captured:
            await kafka_producer.send_and_wait(
                NOTIFICATION_DLQ_TOPIC, key=booking_id.encode(), value=envelope.model_dump_json().encode()
            )

            async def logged() -> bool:
                return any(
                    entry.get("event") == "notification_landed_in_dlq" and entry.get("booking_id") == booking_id
                    for entry in captured
                )

            await _wait_until(logged, timeout=10)

        entry = next(
            e for e in captured if e.get("event") == "notification_landed_in_dlq" and e.get("booking_id") == booking_id
        )
        assert entry["booking_id"] == booking_id
        assert entry["attempt"] == 4
        assert entry["last_error"] == "simulated failure for test_dlq_consumer_logs_receipt"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await consumer.stop()
