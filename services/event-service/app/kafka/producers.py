import uuid

import structlog
from aiokafka import AIOKafkaProducer

from app.api.schemas import SeatMap
from app.core import get_kafka_producer, get_settings
from app.db.models import Event
from app.kafka.schemas import EventDeletedMessage, EventSeat, EventUpsertedMessage

logger = structlog.get_logger()


class EventProducer:
    def __init__(self, producer: AIOKafkaProducer, topic: str):
        self._producer = producer
        self._topic = topic

    async def publish_upserted(self, event: Event, seat_map: SeatMap) -> None:
        seats = [
            EventSeat(section=section.name, row=row.name, label=seat.label)
            for section in seat_map.sections
            for row in section.rows
            for seat in row.seats
        ]
        message = EventUpsertedMessage(
            event_id=event.id,
            title=event.title,
            description=event.description,
            start_time=event.start_time,
            end_time=event.end_time,
            venue_name=event.venue.name,
            performer_names=[performer.name for performer in event.performers],
            seats=seats,
        )
        await self._send(event.id, message)

    async def publish_deleted(self, event_id: uuid.UUID) -> None:
        await self._send(event_id, EventDeletedMessage(event_id=event_id))

    async def _send(self, event_id: uuid.UUID, message: EventUpsertedMessage | EventDeletedMessage) -> None:
        # Keyed by event ID (§7 idempotency) so all messages for one event land on the
        # same partition and stay strictly ordered for a downstream consumer.
        await self._producer.send_and_wait(
            self._topic,
            key=str(event_id).encode(),
            value=message.model_dump_json().encode(),
        )
        logger.info("event_kafka_message_published", event_id=str(event_id), action=message.action.value)


async def get_event_producer() -> EventProducer:
    producer = await get_kafka_producer()
    return EventProducer(producer, get_settings().events_topic)
