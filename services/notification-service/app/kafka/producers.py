from collections.abc import Callable

import structlog
from aiokafka import AIOKafkaProducer

from app.core import get_kafka_producer, get_settings
from app.kafka.schemas import RetryEnvelope

logger = structlog.get_logger()


class RetryPublisher:
    """Publishes to notification-retry / notification-dlq — the two topics
    that carry this service's retry state (decisions-log §17 amendment,
    2026-08-18), since there's no DB to hold it in instead."""

    def __init__(self, producer: AIOKafkaProducer, retry_topic: str, dlq_topic: str):
        self._producer = producer
        self._retry_topic = retry_topic
        self._dlq_topic = dlq_topic

    async def publish_retry(self, envelope: RetryEnvelope) -> None:
        await self._send(
            envelope,
            self._retry_topic,
            logger.info,
            "notification_retry_scheduled",
            next_attempt=envelope.attempt,
        )

    async def publish_dlq(self, envelope: RetryEnvelope) -> None:
        await self._send(
            envelope,
            self._dlq_topic,
            logger.error,
            "notification_routed_to_dlq",
            attempt=envelope.attempt,
            last_error=envelope.last_error,
        )

    async def _send(
        self, envelope: RetryEnvelope, topic: str, log_fn: Callable[..., None], log_event: str, **log_fields
    ) -> None:
        # Keyed by booking ID, same as every other producer here — redelivery/ordering per booking preserved (§7).
        await self._producer.send_and_wait(
            topic,
            key=str(envelope.original.booking_id).encode(),
            value=envelope.model_dump_json().encode(),
        )
        log_fn(
            log_event,
            action=envelope.original.action.value,
            booking_id=str(envelope.original.booking_id),
            **log_fields,
        )


async def get_retry_publisher() -> RetryPublisher:
    producer = await get_kafka_producer()
    settings = get_settings()
    return RetryPublisher(producer, settings.notification_retry_topic, settings.notification_dlq_topic)
