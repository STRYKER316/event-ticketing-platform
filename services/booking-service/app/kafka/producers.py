import uuid

import structlog
from aiokafka import AIOKafkaProducer

from app.core import get_kafka_producer, get_settings
from app.kafka.schemas import BookingCancelledMessage

logger = structlog.get_logger()


class BookingCancelledProducer:
    """This service's first-ever Kafka producer (§22, integration point
    #5) — mirrors payment-service/app/kafka/producers.py's
    PaymentOutcomeProducer shape exactly."""

    def __init__(self, producer: AIOKafkaProducer, topic: str):
        self._producer = producer
        self._topic = topic

    async def publish_cancelled(self, booking_id: uuid.UUID) -> None:
        message = BookingCancelledMessage(booking_id=booking_id)
        # Keyed by booking ID so redelivery/ordering per booking is preserved
        # on the consumer side (§7), same reasoning as every other producer
        # in this system.
        await self._producer.send_and_wait(
            self._topic,
            key=str(booking_id).encode(),
            value=message.model_dump_json().encode(),
        )
        logger.info("booking_cancelled_published", booking_id=str(booking_id))


async def get_booking_cancelled_producer() -> BookingCancelledProducer:
    producer = await get_kafka_producer()
    return BookingCancelledProducer(producer, get_settings().cancelled_bookings_topic)
