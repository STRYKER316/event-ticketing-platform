import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Literal

import httpx
import structlog
from aiokafka import AIOKafkaProducer
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    booking_db_name: str = "booking_db"
    booking_db_user: str = "booking_service"
    booking_db_password: str = "changeme"
    booking_service_port: int = 8003

    redis_host: str = "localhost"
    redis_port: int = 6379

    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9094"
    events_topic: str = "event.events"
    payment_outcomes_topic: str = "payment.outcomes"
    # Integration point #5 (§22) — this service's first-ever Kafka producer.
    cancelled_bookings_topic: str = "booking.cancelled"
    # Integration point #3 (§7 point 3, Phase 5) — shared with payment-service,
    # which already publishes REFUND_FAILED here since P6.T3.
    notifications_topic: str = "notifications"
    kafka_consumer_group_id: str = "booking-service"
    # Separate from kafka_consumer_group_id: sharing one group id across
    # both consumers meant every payment.outcomes rebalance also rebalanced
    # the (unrelated) provisioning consumer's event.events subscription,
    # and vice versa.
    payment_outcome_consumer_group_id: str = "booking-service-payment-outcomes"

    hold_strategy: Literal["cron", "redis"] = "cron"  # Phase 8 benchmark toggles this
    hold_ttl_seconds: int = 600
    hold_sweep_interval_seconds: int = 30

    # Booking Service fronts payment (decisions-log §9 amendment) — the one
    # synchronous inter-service call in this system.
    payment_service_url: str = "http://localhost:8004"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.booking_db_user}:{self.booking_db_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.booking_db_name}"
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


_redis: Redis | None = None
_redis_lock = asyncio.Lock()


async def get_redis() -> Redis:
    global _redis
    # Unlike get_engine(), this has an `await` between the check and the
    # assignment, so two concurrent first callers can otherwise both start a
    # client — the loser's connection is then never closed.
    async with _redis_lock:
        if _redis is None:
            _redis = Redis(host=get_settings().redis_host, port=get_settings().redis_port, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """Used for the one synchronous inter-service call in this system —
    Booking Service calling Payment Service's charge endpoint (decisions-log
    §9 amendment). A short timeout fails fast rather than holding a
    request-path connection open indefinitely if Payment Service is down."""
    global _http_client
    async with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.AsyncClient(timeout=10.0)
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


_kafka_producer: AIOKafkaProducer | None = None
_kafka_producer_lock = asyncio.Lock()


async def get_kafka_producer() -> AIOKafkaProducer:
    """This service has only ever consumed Kafka until now (§22, integration
    point #5) — mirrors payment-service/app/core.py's own producer
    singleton exactly."""
    global _kafka_producer
    async with _kafka_producer_lock:
        if _kafka_producer is None:
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
