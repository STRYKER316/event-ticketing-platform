import uuid

import structlog
from aiokafka import AIOKafkaProducer

from app.core import get_kafka_producer, get_settings
from app.db.models import Payment, PaymentStatus
from app.kafka.schemas import NotificationAction, NotificationMessage, PaymentOutcomeAction, PaymentOutcomeMessage

logger = structlog.get_logger()


class _KafkaMessageProducer:
    """Shared send-and-log plumbing for this service's producers — each
    subclass only differs in the message it builds and the log event name."""

    def __init__(self, producer: AIOKafkaProducer, topic: str):
        self._producer = producer
        self._topic = topic

    async def _send(self, booking_id: uuid.UUID, message: PaymentOutcomeMessage | NotificationMessage, log_event: str) -> None:
        # Keyed by booking ID so redelivery/ordering per booking is preserved on the consumer side (§7), same reasoning as event-service keying by event ID.
        await self._producer.send_and_wait(
            self._topic,
            key=str(booking_id).encode(),
            value=message.model_dump_json().encode(),
        )
        logger.info(log_event, booking_id=str(booking_id), action=message.action.value)


class PaymentOutcomeProducer(_KafkaMessageProducer):
    async def publish_outcome(self, payment: Payment) -> None:
        action = PaymentOutcomeAction.SUCCEEDED if payment.status is PaymentStatus.SUCCEEDED else PaymentOutcomeAction.FAILED
        message = PaymentOutcomeMessage(action=action, booking_id=payment.booking_id, ticket_id=payment.ticket_id)
        await self._send(payment.booking_id, message, "payment_outcome_published")


async def get_payment_outcome_producer() -> PaymentOutcomeProducer:
    producer = await get_kafka_producer()
    return PaymentOutcomeProducer(producer, get_settings().payment_outcomes_topic)


class NotificationProducer(_KafkaMessageProducer):
    async def publish_refund_failed(self, booking_id: uuid.UUID, reason: str) -> None:
        message = NotificationMessage(action=NotificationAction.REFUND_FAILED, booking_id=booking_id, reason=reason)
        await self._send(booking_id, message, "notification_published")

    async def publish_payment_confirmed(self, booking_id: uuid.UUID) -> None:
        message = NotificationMessage(action=NotificationAction.PAYMENT_CONFIRMED, booking_id=booking_id)
        await self._send(booking_id, message, "notification_published")


async def get_notification_producer() -> NotificationProducer:
    producer = await get_kafka_producer()
    return NotificationProducer(producer, get_settings().notifications_topic)
