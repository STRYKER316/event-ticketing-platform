from collections.abc import AsyncIterator

import pytest
from aiokafka import AIOKafkaProducer
from elasticsearch import AsyncElasticsearch
from testcontainers.community.elasticsearch import ElasticSearchContainer
from testcontainers.community.kafka import KafkaContainer

EVENTS_TOPIC = "event.events"


@pytest.fixture(scope="session")
def es_container() -> "ElasticSearchContainer":
    with ElasticSearchContainer("elasticsearch:8.15.0", mem_limit="1G") as container:
        yield container


@pytest.fixture(scope="session")
def kafka_container() -> "KafkaContainer":
    # testcontainers' KafkaContainer targets the Confluent image's bootstrap scripts — the compose stack's apache/kafka image isn't compatible, so tests use Confluent's image in KRaft mode.
    with KafkaContainer("confluentinc/cp-kafka:7.6.0").with_kraft() as container:
        yield container


@pytest.fixture
async def es_client(es_container: "ElasticSearchContainer") -> AsyncIterator[AsyncElasticsearch]:
    client = AsyncElasticsearch(
        hosts=[f"http://{es_container.get_container_host_ip()}:{es_container.get_exposed_port(es_container.port)}"],
        request_timeout=30,
    )
    yield client
    await client.indices.delete(index="events", ignore_unavailable=True)
    await client.close()


@pytest.fixture
async def kafka_producer(kafka_container: "KafkaContainer") -> AsyncIterator[AIOKafkaProducer]:
    producer = AIOKafkaProducer(bootstrap_servers=kafka_container.get_bootstrap_server())
    await producer.start()
    yield producer
    await producer.stop()
