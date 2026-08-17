import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from functools import lru_cache

import structlog
from aiokafka import AIOKafkaProducer
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    payment_db_name: str = "payment_db"
    payment_db_user: str = "payment_service"
    payment_db_password: str = "changeme"
    payment_service_port: int = 8004

    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9094"
    payment_outcomes_topic: str = "payment.outcomes"

    stripe_secret_key: str = "sk_test_changeme"
    stripe_webhook_secret: str = "whsec_changeme"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.payment_db_user}:{self.payment_db_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.payment_db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging() -> None:
    """Route both structlog calls and stdlib logging (incl. uvicorn's) through
    the same JSON renderer, so every log line — app and access logs alike —
    comes out as JSON."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    level = logging.getLevelNamesMapping().get(get_settings().log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


_kafka_producer: AIOKafkaProducer | None = None
_kafka_producer_lock = asyncio.Lock()


async def get_kafka_producer() -> AIOKafkaProducer:
    global _kafka_producer
    # Unlike get_engine(), this has an `await` between the check and the
    # assignment, so two concurrent first callers can otherwise both start a
    # producer — the loser's connection is then never stopped.
    async with _kafka_producer_lock:
        if _kafka_producer is None:
            # aiokafka's default request_timeout_ms is 40000 -- under a broker outage
            # that leaves a publish-triggering request hanging for 40s before the
            # client sees a failure. 10s still tolerates real broker slowness while
            # failing fast enough to matter (same value as event-service's producer).
            producer = AIOKafkaProducer(
                bootstrap_servers=get_settings().kafka_bootstrap_servers, request_timeout_ms=10_000
            )
            await producer.start()
            _kafka_producer = producer
    return _kafka_producer


async def close_kafka_producer() -> None:
    global _kafka_producer
    if _kafka_producer is not None:
        await _kafka_producer.stop()
        _kafka_producer = None
