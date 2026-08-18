from collections.abc import Awaitable, Callable

import structlog
from aiokafka import AIOKafkaConsumer

from app.core import get_settings
from app.kafka.schemas import NotificationMessage
from app.logic.notification_manager import NotificationManager

logger = structlog.get_logger()


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


def build_notification_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.notifications_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.notification_consumer_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )


class NotificationConsumer:
    """No equivalent API route delivers a notification from a Kafka
    payload — goes straight to NotificationManager rather than through a
    second entry point, same reasoning ProvisioningConsumer's own
    docstring gives in booking-service."""

    def __init__(self, consumer: AIOKafkaConsumer):
        self._consumer = consumer

    async def run(self) -> None:
        await _consume_with_manual_commit(self._consumer, self._handle)

    async def _handle(self, raw: bytes) -> None:
        try:
            message = NotificationMessage.model_validate_json(raw)
        except Exception:
            # Unretriable poison message — a malformed payload won't become
            # parseable on retry, so it never enters the retry ladder at
            # all, same handling ProvisioningConsumer/PaymentOutcomeConsumer
            # already use for their own parse failures.
            logger.error("notification_consumer_message_invalid", raw=raw[:500], exc_info=True)
            return

        try:
            await NotificationManager().deliver(message, attempt=1)
        except Exception as exc:
            # The retry-ladder republish is P5.T3, not this task — this
            # task only logs the failure and returns, matching §17's
            # "confirmation 'sent' (logged) on real events" scope.
            logger.warning(
                "notification_delivery_failed",
                action=message.action.value,
                booking_id=str(message.booking_id),
                attempt=1,
                error=str(exc),
            )
