import json
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer

from app.core import get_settings
from app.db.event_index_repository import EventIndexRepository
from app.kafka.schemas import EventDeletedMessage, EventUpsertedMessage, KafkaAction

logger = structlog.get_logger()


def build_kafka_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_id,
        auto_offset_reset="earliest",
    )


def _to_document(message: EventUpsertedMessage) -> dict[str, Any]:
    return {
        "event_id": str(message.event_id),
        "title": message.title,
        "description": message.description,
        "start_time": message.start_time.isoformat(),
        "end_time": message.end_time.isoformat(),
        "venue_name": message.venue_name,
        "performer_names": message.performer_names,
        "seats": [seat.model_dump() for seat in message.seats],
    }


class EventConsumer:
    def __init__(self, consumer: AIOKafkaConsumer, repository: EventIndexRepository):
        self._consumer = consumer
        self._repository = repository

    async def run(self) -> None:
        async for record in self._consumer:
            await self._handle(record.value)

    async def _handle(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw)
            # payload.get() assumes a JSON object; valid JSON that isn't one
            # (a bare list/string/number/null) raises AttributeError here,
            # which must not escape and kill the background consumer task.
            action = KafkaAction(payload.get("action"))
        except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
            logger.error("search_consumer_message_unparseable", error=str(exc), raw=raw[:500])
            return

        try:
            if action is KafkaAction.DELETED:
                message = EventDeletedMessage.model_validate(payload)
                await self._repository.delete(str(message.event_id))
                logger.info("search_index_deleted", event_id=str(message.event_id))
            else:
                message = EventUpsertedMessage.model_validate(payload)
                await self._repository.upsert(str(message.event_id), _to_document(message))
                logger.info("search_index_upserted", event_id=str(message.event_id))
        except Exception:
            logger.error("search_consumer_message_failed", action=action.value, raw=raw[:500], exc_info=True)
