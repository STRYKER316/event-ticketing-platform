import uuid

import structlog
from aiokafka import AIOKafkaProducer

from app.core import get_kafka_producer, get_settings
from app.kafka.schemas import BookingCancelledMessage, NotificationAction, NotificationMessage

logger = structlog.get_logger()


class _KafkaMessageProducer:
    """Shared send-and-log plumbing for this service's producers — mirrors
    payment-service/app/kafka/producers.py's _KafkaMessageProducer of the
    same name."""

    def __init__(self, producer: AIOKafkaProducer, topic: str):
        self._producer = producer
        self._topic = topic

    async def _send(self, booking_id: uuid.UUID, message: BookingCancelledMessage | NotificationMessage, log_event: str) -> None:
        # Keyed by booking ID so redelivery/ordering per booking is preserved
        # on the consumer side (§7), same reasoning as every other producer
        # in this system.
        await self._producer.send_and_wait(
            self._topic,
            key=str(booking_id).encode(),
            value=message.model_dump_json().encode(),
        )
        log_fields = {"action": message.action.value} if isinstance(message, NotificationMessage) else {}
        logger.info(log_event, booking_id=str(booking_id), **log_fields)


class BookingCancelledProducer(_KafkaMessageProducer):
    """This service's first-ever Kafka producer (§22, integration point
    #5) — mirrors payment-service/app/kafka/producers.py's
    PaymentOutcomeProducer shape exactly."""

    async def publish_cancelled(self, booking_id: uuid.UUID) -> None:
        message = BookingCancelledMessage(booking_id=booking_id)
        await self._send(booking_id, message, "booking_cancelled_published")


async def get_booking_cancelled_producer() -> BookingCancelledProducer:
    producer = await get_kafka_producer()
    return BookingCancelledProducer(producer, get_settings().cancelled_bookings_topic)


class NotificationProducer(_KafkaMessageProducer):
    """Integration point #3 (§7 point 3, Phase 5) — this service's second
    producer, publishing to the same notifications topic payment-service
    has published REFUND_FAILED to since P6.T3."""

    async def publish_booking_confirmed(self, booking_id: uuid.UUID) -> None:
        message = NotificationMessage(action=NotificationAction.BOOKING_CONFIRMED, booking_id=booking_id)
        await self._send(booking_id, message, "notification_published")


async def get_notification_producer() -> NotificationProducer:
    producer = await get_kafka_producer()
    return NotificationProducer(producer, get_settings().notifications_topic)
