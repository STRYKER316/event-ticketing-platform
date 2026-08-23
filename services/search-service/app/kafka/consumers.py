import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer

from app.core import get_settings
from app.db.event_index_repository import EventIndexRepository
from app.kafka.schemas import EventDeletedMessage, EventUpsertedMessage, KafkaAction

logger = structlog.get_logger()

# Retries a transient ES failure a few times before giving up — without this, the per-record offset commit (enable_auto_commit below) would advance past a message whose write never succeeded, silently losing it.
ES_WRITE_MAX_ATTEMPTS = 3
ES_WRITE_RETRY_BACKOFF_SECONDS = 1.0


def build_kafka_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_id,
        auto_offset_reset="earliest",
        # Manual commit — default auto-commit would advance past a crash mid-write; offset commits in run() only after _handle() finishes.
        enable_auto_commit=False,
    )


async def _run_with_retry(
    operation: Callable[[], Awaitable[None]],
    *,
    retrying_event: str,
    failed_event: str,
    **log_context: object,
) -> bool:
    """Bounded-retry shape mirroring booking-service's consumers._run_with_retry:
    retries a transient failure in place a few times with backoff before
    giving up, logging critical (not silently) on the last attempt rather
    than raising. Returns True once `operation` succeeds, False once every
    attempt has failed — the caller commits the Kafka offset regardless
    either way (see build_kafka_consumer's enable_auto_commit note)."""
    for attempt in range(1, ES_WRITE_MAX_ATTEMPTS + 1):
        try:
            await operation()
            return True
        except Exception:
            if attempt == ES_WRITE_MAX_ATTEMPTS:
                logger.critical(failed_event, attempts=attempt, exc_info=True, **log_context)
                return False
            logger.warning(retrying_event, attempt=attempt, exc_info=True, **log_context)
            await asyncio.sleep(ES_WRITE_RETRY_BACKOFF_SECONDS)
    return False


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
            # Offset commits only after _handle() fully finishes — see build_kafka_consumer's enable_auto_commit note.
            await self._consumer.commit()

    async def _handle(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw)
            # payload.get() assumes a JSON object; a bare list/string/number/null raises AttributeError here, which must not escape and kill the consumer task.
            action = KafkaAction(payload.get("action"))
        except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
            logger.warning("search_consumer_message_unparseable", error=str(exc), raw=raw[:500])
            return

        if action is KafkaAction.DELETED:
            try:
                message: EventDeletedMessage | EventUpsertedMessage = EventDeletedMessage.model_validate(payload)
            except Exception:
                logger.warning("search_consumer_message_failed", action=action.value, raw=raw[:500], exc_info=True)
                return
            await self._delete_with_retry(message)
        else:
            try:
                message = EventUpsertedMessage.model_validate(payload)
            except Exception:
                logger.warning("search_consumer_message_failed", action=action.value, raw=raw[:500], exc_info=True)
                return
            await self._upsert_with_retry(message)

    async def _upsert_with_retry(self, message: EventUpsertedMessage) -> None:
        event_id = str(message.event_id)
        document = _to_document(message)

        async def _write() -> None:
            await self._repository.upsert(event_id, document)

        succeeded = await _run_with_retry(
            _write,
            retrying_event="search_consumer_es_write_failed_retrying",
            failed_event="search_consumer_es_write_failed_permanently",
            event_id=event_id,
        )
        if succeeded:
            logger.info("search_index_upserted", event_id=event_id)

    async def _delete_with_retry(self, message: EventDeletedMessage) -> None:
        event_id = str(message.event_id)

        async def _write() -> None:
            await self._repository.delete(event_id)

        succeeded = await _run_with_retry(
            _write,
            retrying_event="search_consumer_es_delete_failed_retrying",
            failed_event="search_consumer_es_delete_failed_permanently",
            event_id=event_id,
        )
        if succeeded:
            logger.info("search_index_deleted", event_id=event_id)
