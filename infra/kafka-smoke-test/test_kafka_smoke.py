"""Throwaway round-trip proving the compose Kafka broker works end-to-end (§7).

Not wired into any service — isolated smoke test only. Run against the
running compose stack:

    docker compose --env-file ../../.env up -d kafka
    uv run pytest
"""

import asyncio
import json
import os
import uuid

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "localhost:9092")


@pytest.mark.asyncio
async def test_produce_and_consume_round_trip():
    # Unique per run: a shared topic name would let a stale message from a
    # prior run satisfy `earliest` before the one this run just produced.
    topic = f"phase0-smoke-test-{uuid.uuid4()}"
    message = {"id": str(uuid.uuid4()), "text": "phase 0 kafka smoke test"}

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=KAFKA_BROKER,
        group_id=f"smoke-test-{uuid.uuid4()}",
        auto_offset_reset="earliest",
        value_deserializer=lambda v: json.loads(v.decode()),
    )
    await consumer.start()

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BROKER,
        value_serializer=lambda v: json.dumps(v).encode(),
    )
    await producer.start()

    try:
        await producer.send_and_wait(topic, message)

        received = await asyncio.wait_for(consumer.getone(), timeout=20)
        assert received.value == message
    finally:
        await producer.stop()
        await consumer.stop()
