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

# Bounded retry for a transient republish failure — this service's no-DB equivalent of "must finish before offset commit" (§17 amendment).
PUBLISH_MAX_ATTEMPTS = 3
PUBLISH_RETRY_BACKOFF_SECONDS = 1.0


async def _publish_with_retry(
    operation: Callable[[], Awaitable[None]],
    *,
    retrying_event: str,
    failed_event: str,
    **log_context: object,
) -> None:
    """Retries a transient republish failure before giving up; after the
    last attempt, logs critical and returns rather than raising — same
    give-up-and-move-on trade-off _run_with_retry documents, so the caller's
    offset still commits instead of blocking the topic on a permanently
    unreachable broker."""
    for attempt in range(1, PUBLISH_MAX_ATTEMPTS + 1):
        try:
            await operation()
            return
        except Exception:
            if attempt == PUBLISH_MAX_ATTEMPTS:
                logger.critical(failed_event, attempts=attempt, exc_info=True, **log_context)
                return
            logger.warning(retrying_event, attempt=attempt, exc_info=True, **log_context)
            await asyncio.sleep(PUBLISH_RETRY_BACKOFF_SECONDS)


def _error_text(exc: Exception) -> str:
    """RetryEnvelope.last_error is a NonBlankStr; some exceptions stringify
    to '' (e.g. bare KeyError()), which would raise ValidationError outside
    every _publish_with_retry guard and kill the consumer task. Checked
    against the stripped string, not the raw one — a whitespace-only
    message is truthy here but still collapses to "" past the envelope's
    own strip."""
    text = str(exc)
    return text if text.strip() else repr(exc)


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
        # Unretriable poison message on parse failure — same handling ProvisioningConsumer/PaymentOutcomeConsumer use.
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
                reason=message.reason,
            )
            # attempt=2 is the next attempt to be made; published before the offset commits, so a crash here redelivers rather than drops the message.
            envelope = RetryEnvelope(attempt=2, original=message, last_error=_error_text(exc))
            await _publish_with_retry(
                lambda: self._retry_publisher.publish_retry(envelope),
                retrying_event="notification_consumer_retry_publish_failed_retrying",
                failed_event="notification_consumer_retry_publish_failed_permanently",
                booking_id=str(message.booking_id),
            )


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

        # Real in-process sleep — aiokafka has no native delayed-delivery primitive, and this is hand-rolled, not @RetryableTopic.
        await asyncio.sleep(compute_backoff_seconds(envelope.attempt))

        try:
            await NotificationManager().deliver(envelope.original, attempt=envelope.attempt)
        except Exception as exc:
            settings = get_settings()
            booking_id = str(envelope.original.booking_id)
            if envelope.attempt > settings.retry_max_attempts:
                dlq_envelope = RetryEnvelope(
                    attempt=envelope.attempt, original=envelope.original, last_error=_error_text(exc)
                )
                await _publish_with_retry(
                    lambda: self._retry_publisher.publish_dlq(dlq_envelope),
                    retrying_event="retry_consumer_dlq_publish_failed_retrying",
                    failed_event="retry_consumer_dlq_publish_failed_permanently",
                    booking_id=booking_id,
                )
            else:
                next_envelope = RetryEnvelope(
                    attempt=envelope.attempt + 1, original=envelope.original, last_error=_error_text(exc)
                )
                await _publish_with_retry(
                    lambda: self._retry_publisher.publish_retry(next_envelope),
                    retrying_event="retry_consumer_retry_publish_failed_retrying",
                    failed_event="retry_consumer_retry_publish_failed_permanently",
                    booking_id=booking_id,
                )
            return

        logger.info(
            "notification_delivered_after_retry",
            action=envelope.original.action.value,
            booking_id=str(envelope.original.booking_id),
            attempt=envelope.attempt,
            reason=envelope.original.reason,
        )


def build_dlq_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return _build_consumer(settings.notification_dlq_topic, settings.notification_dlq_consumer_group_id)


class DlqConsumer:
    """Visibility only (§17 amendment #2) — nothing reprocesses out of the
    DLQ automatically. This matches what that amendment actually promises:
    'not silently dropped,' not 'automatically retried forever.'"""

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
            reason=envelope.original.reason,
        )
