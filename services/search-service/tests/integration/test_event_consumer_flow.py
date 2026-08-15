import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from elasticsearch import AsyncElasticsearch

from app.db.event_index_repository import EventIndexRepository
from app.kafka.consumers import EventConsumer
from tests.integration.conftest import EVENTS_TOPIC

pytestmark = pytest.mark.asyncio


async def _wait_until(predicate, timeout: float = 15, interval: float = 0.5) -> None:
    async def poll():
        while not await predicate():
            await asyncio.sleep(interval)

    await asyncio.wait_for(poll(), timeout=timeout)


@pytest.fixture
async def running_consumer(kafka_container, es_client: AsyncElasticsearch) -> AsyncIterator[EventIndexRepository]:
    repository = EventIndexRepository(es_client)
    await repository.ensure_index()

    kafka_consumer = AIOKafkaConsumer(
        EVENTS_TOPIC,
        bootstrap_servers=kafka_container.get_bootstrap_server(),
        group_id=f"search-service-test-{uuid.uuid4()}",
        auto_offset_reset="earliest",
    )
    await kafka_consumer.start()
    task = asyncio.create_task(EventConsumer(kafka_consumer, repository).run())

    yield repository

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await kafka_consumer.stop()


async def test_publish_makes_event_searchable_and_redelivery_is_a_noop(
    kafka_producer: AIOKafkaProducer, running_consumer: EventIndexRepository, es_client: AsyncElasticsearch
):
    event_id = str(uuid.uuid4())
    message = {
        "action": "upserted",
        "event_id": event_id,
        "title": "Integration Event",
        "description": None,
        "start_time": "2026-09-01T10:00:00Z",
        "end_time": "2026-09-01T12:00:00Z",
        "venue_name": "Integration Venue",
        "performer_names": [],
        "seats": [{"section": "A", "row": "1", "label": "A1"}],
    }

    # Eventual consistency (§7): not indexed the instant it's published — only
    # once the consumer has actually processed the message.
    await kafka_producer.send_and_wait(EVENTS_TOPIC, key=event_id.encode(), value=json.dumps(message).encode())
    assert not await es_client.exists(index="events", id=event_id)

    await _wait_until(lambda: es_client.exists(index="events", id=event_id))
    doc = await es_client.get(index="events", id=event_id)
    assert doc["_source"]["title"] == "Integration Event"
    first_version = doc["_version"]

    # Redelivery of the identical message must not create a duplicate document.
    await kafka_producer.send_and_wait(EVENTS_TOPIC, key=event_id.encode(), value=json.dumps(message).encode())

    async def version_advanced():
        current = await es_client.get(index="events", id=event_id)
        return current["_version"] > first_version

    await _wait_until(version_advanced)
    await es_client.indices.refresh(index="events")
    count = await es_client.count(index="events")
    assert count["count"] == 1


async def test_delete_removes_document_and_redelivered_delete_is_a_noop(
    kafka_producer: AIOKafkaProducer, running_consumer: EventIndexRepository, es_client: AsyncElasticsearch
):
    event_id = str(uuid.uuid4())
    upsert_message = {
        "action": "upserted",
        "event_id": event_id,
        "title": "To Be Deleted",
        "description": None,
        "start_time": "2026-09-01T10:00:00Z",
        "end_time": "2026-09-01T12:00:00Z",
        "venue_name": "Integration Venue",
        "performer_names": [],
        "seats": [],
    }
    await kafka_producer.send_and_wait(
        EVENTS_TOPIC, key=event_id.encode(), value=json.dumps(upsert_message).encode()
    )
    await _wait_until(lambda: es_client.exists(index="events", id=event_id))

    delete_message = {"action": "deleted", "event_id": event_id}
    await kafka_producer.send_and_wait(
        EVENTS_TOPIC, key=event_id.encode(), value=json.dumps(delete_message).encode()
    )

    async def deleted():
        return not await es_client.exists(index="events", id=event_id)

    await _wait_until(deleted)

    # A redelivered delete for an already-deleted document must not raise or
    # otherwise disrupt the consumer loop.
    await kafka_producer.send_and_wait(
        EVENTS_TOPIC, key=event_id.encode(), value=json.dumps(delete_message).encode()
    )
    await asyncio.sleep(2)
    assert not await es_client.exists(index="events", id=event_id)
