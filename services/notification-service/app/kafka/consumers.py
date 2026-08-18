import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog
from aiokafka import AIOKafkaConsumer
from pydantic import BaseModel

from app.core import get_settings
from app.kafka.producers import RetryPublisher
from app.kafka.schemas import NotificationMessage, RetryEnvelope
from app.logic.helpers.backoff import compute_backoff_seconds
from app.logic.notification_manager import NotificationManager

logger = structlog.get_logger()

_M = TypeVar("_M", bound=BaseModel)


def _parse_or_log(model_cls: type[_M], raw: bytes, invalid_event: str) -> _M | None:
    """Shared parse-or-log-and-drop step for all three consumers below — a
    malformed payload won't become parseable on retry, so it never enters
    the retry ladder at all, same handling every consumer here needs."""
    try:
        return model_cls.model_validate_json(raw)
    except Exception:
        logger.error(invalid_event, raw=raw[:500], exc_info=True)
        return None


async def _consume_with_manual_commit(consumer: AIOKafkaConsumer, handle: Callable[[bytes], Awaitable[None]]) -> None:
    """Commits only after `handle` has fully finished with the record — same
    reasoning as every other service's consumer loop in this system (see
    booking-service/app/kafka/consumers.py's build_kafka_consumer() note),
    just applied here to a Kafka republish standing in for a DB write as
    'the thing that must finish before the offset advances' (this service
    has no DB, decisions-log §17 amendment)."""
    async for record in consumer:
        await handle(record.value)
        await consumer.commit()


def _build_consumer(topic: str, group_id: str) -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )


def build_notification_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return _build_consumer(settings.notifications_topic, settings.notification_consumer_group_id)


class NotificationConsumer:
    """No equivalent API route delivers a notification from a Kafka
    payload — goes straight to NotificationManager rather than through a
    second entry point, same reasoning ProvisioningConsumer's own
    docstring gives in booking-service."""

    def __init__(self, consumer: AIOKafkaConsumer, retry_publisher: RetryPublisher):
        self._consumer = consumer
        self._retry_publisher = retry_publisher

    async def run(self) -> None:
        await _consume_with_manual_commit(self._consumer, self._handle)

    async def _handle(self, raw: bytes) -> None:
        # Unretriable poison message on parse failure — same handling
        # ProvisioningConsumer/PaymentOutcomeConsumer already use for their
        # own parse failures.
        message = _parse_or_log(NotificationMessage, raw, "notification_consumer_message_invalid")
        if message is None:
            return

        try:
            await NotificationManager().deliver(message, attempt=1)
        except Exception as exc:
            logger.warning(
                "notification_delivery_failed",
                action=message.action.value,
                booking_id=str(message.booking_id),
                attempt=1,
                error=str(exc),
            )
            # attempt=2 — the next attempt about to be made (§17 amendment
            # #2). Published before this record's offset commits (see
            # _consume_with_manual_commit above) — a crash between the
            # failed delivery and this publish must redeliver from
            # `notifications`, not silently drop the message.
            envelope = RetryEnvelope(attempt=2, original=message, last_error=str(exc))
            await self._retry_publisher.publish_retry(envelope)


def build_retry_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return _build_consumer(settings.notification_retry_topic, settings.notification_retry_consumer_group_id)


class RetryConsumer:
    def __init__(self, consumer: AIOKafkaConsumer, retry_publisher: RetryPublisher):
        self._consumer = consumer
        self._retry_publisher = retry_publisher

    async def run(self) -> None:
        await _consume_with_manual_commit(self._consumer, self._handle)

    async def _handle(self, raw: bytes) -> None:
        envelope = _parse_or_log(RetryEnvelope, raw, "retry_consumer_message_invalid")
        if envelope is None:
            return

        # A real, in-process asyncio.sleep — this consumer has no other
        # work competing for its attention while backing off, and aiokafka
        # has no native delayed-delivery primitive to reach for instead
        # (this phase's whole "hand-rolled, not @RetryableTopic" framing).
        await asyncio.sleep(compute_backoff_seconds(envelope.attempt))

        try:
            await NotificationManager().deliver(envelope.original, attempt=envelope.attempt)
        except Exception as exc:
            settings = get_settings()
            if envelope.attempt > settings.retry_max_attempts:
                await self._retry_publisher.publish_dlq(
                    RetryEnvelope(attempt=envelope.attempt, original=envelope.original, last_error=str(exc))
                )
            else:
                await self._retry_publisher.publish_retry(
                    RetryEnvelope(attempt=envelope.attempt + 1, original=envelope.original, last_error=str(exc))
                )
            return

        logger.info(
            "notification_delivered_after_retry",
            action=envelope.original.action.value,
            booking_id=str(envelope.original.booking_id),
            attempt=envelope.attempt,
        )


def build_dlq_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return _build_consumer(settings.notification_dlq_topic, settings.notification_dlq_consumer_group_id)


class DlqConsumer:
    """Visibility only (§17 amendment #2) — nothing reprocesses out of the
    DLQ automatically. Matches what §17 actually promises: 'not silently
    dropped,' not 'automatically retried forever.'"""

    def __init__(self, consumer: AIOKafkaConsumer):
        self._consumer = consumer

    async def run(self) -> None:
        await _consume_with_manual_commit(self._consumer, self._handle)

    async def _handle(self, raw: bytes) -> None:
        envelope = _parse_or_log(RetryEnvelope, raw, "dlq_consumer_message_invalid")
        if envelope is None:
            return

        logger.error(
            "notification_landed_in_dlq",
            action=envelope.original.action.value,
            booking_id=str(envelope.original.booking_id),
            attempt=envelope.attempt,
            last_error=envelope.last_error,
        )
