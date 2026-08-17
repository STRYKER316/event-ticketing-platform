from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16", driver="asyncpg") as container:
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
        await conn.execute(text("DELETE FROM payments"))
    await engine.dispose()


@pytest.fixture
def db_session_factory(_migrated_database_url: str) -> async_sessionmaker[AsyncSession]:
    """For tests needing multiple independent, concurrently-open sessions
    (e.g. a real race between two overlapping webhook deliveries) — a
    single shared `db_session` can't model that."""
    engine: AsyncEngine = create_async_engine(_migrated_database_url)
    return async_sessionmaker(engine, expire_on_commit=False)
