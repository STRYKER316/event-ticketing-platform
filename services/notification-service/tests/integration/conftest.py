import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Protocol

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from testcontainers.community.kafka import KafkaContainer

from app.core import get_settings

NOTIFICATIONS_TOPIC = "notifications"
NOTIFICATION_RETRY_TOPIC = "notification-retry"
NOTIFICATION_DLQ_TOPIC = "notification-dlq"


class _Runnable(Protocol):
    async def run(self) -> None: ...


@pytest.fixture(scope="session")
def kafka_container() -> "KafkaContainer":
    # Confluent image + .with_kraft() — the compose stack's apache/kafka
    # image isn't compatible with testcontainers' KafkaContainer (CLAUDE.md's
    # corrected Conventions note), same combination every other service's
    # suite already uses.
    with KafkaContainer("confluentinc/cp-kafka:7.6.0").with_kraft() as container:
        yield container


@pytest.fixture
async def kafka_producer(kafka_container: "KafkaContainer") -> AsyncIterator[AIOKafkaProducer]:
    producer = AIOKafkaProducer(bootstrap_servers=kafka_container.get_bootstrap_server())
    await producer.start()
    yield producer
    await producer.stop()


async def make_topic_consumer(kafka_container: "KafkaContainer", topic: str) -> AIOKafkaConsumer:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        group_id=f"notification-service-test-{uuid.uuid4()}",
        auto_offset_reset="earliest",
    )
    await consumer.start()
    return consumer


@contextlib.asynccontextmanager
async def running_consumer(
    kafka_container: "KafkaContainer", topic: str, build_runner: Callable[[AIOKafkaConsumer], _Runnable]
) -> AsyncIterator[None]:
    """Starts a consumer on `topic` driving the runner `build_runner(consumer)`
    returns, tears both down on exit — shared shape for every 'consumer
    under test' fixture in this suite (notification/retry/dlq all start,
    run, and tear down identically; only the topic and wrapper class
    differ)."""
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        group_id=f"notification-service-test-{uuid.uuid4()}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    task = asyncio.create_task(build_runner(consumer).run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await consumer.stop()


@pytest.fixture
def fast_retry_settings():
    """Overrides retry timing to keep integration tests fast — the retry
    ladder's real backoff formula is already covered by unit tests
    (test_backoff.py); these tests only need it to be short."""
    settings = get_settings()
    original = (
        settings.retry_base_backoff_seconds,
        settings.retry_backoff_cap_seconds,
        settings.retry_max_attempts,
        settings.simulated_failure_attempts,
    )
    settings.retry_base_backoff_seconds = 1.05
    settings.retry_backoff_cap_seconds = 2.0
    settings.retry_max_attempts = 1
    settings.simulated_failure_attempts = 0
    yield settings
    (
        settings.retry_base_backoff_seconds,
        settings.retry_backoff_cap_seconds,
        settings.retry_max_attempts,
        settings.simulated_failure_attempts,
    ) = original
