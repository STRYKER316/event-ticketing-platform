import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.db.models import Ticket, TicketStatus


@pytest.fixture(scope="session")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16", driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def kafka_container() -> "KafkaContainer":
    with KafkaContainer("apache/kafka:3.8.0") as container:
        yield container


@pytest.fixture(scope="session")
def _migrated_database_url(postgres_container: "PostgresContainer") -> str:
    url = postgres_container.get_connection_url()
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", "migrations")
    alembic_cfg.attributes["sqlalchemy_url"] = url
    command.upgrade(alembic_cfg, "head")
    return url


@pytest.fixture
async def db_session(_migrated_database_url: str) -> AsyncIterator[AsyncSession]:
    engine: AsyncEngine = create_async_engine(_migrated_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        for table in ("bookings", "tickets"):
            await conn.execute(text(f"DELETE FROM {table}"))
    await engine.dispose()


@pytest.fixture
def db_session_factory(_migrated_database_url: str) -> async_sessionmaker[AsyncSession]:
    """For code paths (the provisioning consumer, the hold sweep) that open
    their own session per unit of work rather than taking one via Depends()."""
    engine: AsyncEngine = create_async_engine(_migrated_database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def redis_client(redis_container: "RedisContainer") -> AsyncIterator[Redis]:
    """Shared across every integration test module that needs a live Redis
    connection, rather than each one re-deriving a client from
    redis_container inline."""
    client = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(redis_container.port)),
        decode_responses=True,
    )
    yield client
    await client.flushall()
    await client.aclose()


async def seed_ticket(session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """Shared across integration test modules that just need one AVAILABLE
    ticket seeded — not a pytest fixture since callers pass their own
    db_session_factory explicitly (used inside asyncio.gather in the
    concurrency suite, where fixture injection doesn't apply)."""
    async with session_factory() as session:
        ticket = Ticket(event_id=uuid.uuid4(), section="A", row_name="1", seat_label="A1", status=TicketStatus.AVAILABLE)
        session.add(ticket)
        await session.commit()
        return ticket.id
