import asyncio
import logging
import sys
from functools import lru_cache
from typing import Annotated

import structlog
from aiokafka import AIOKafkaProducer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    notification_service_port: int = 8005

    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9094"
    notifications_topic: str = "notifications"
    notification_consumer_group_id: str = "notification-service-notifications"
    # §17 amendment retry/DLQ topics — retry state travels on the message (RetryEnvelope), not a table, since this service has no DB.
    notification_retry_topic: str = "notification-retry"
    notification_retry_consumer_group_id: str = "notification-service-retry"
    notification_dlq_topic: str = "notification-dlq"
    notification_dlq_consumer_group_id: str = "notification-service-dlq"

    # attempt starts at 2 for the first retry (initial delivery is attempt 1); attempt > retry_max_attempts routes to the DLQ. Backoff: min(base**attempt, cap).
    # le=1000 bound matches RetryEnvelope.attempt's own le=1000 so a bad config fails at startup, not at the first retry.
    retry_max_attempts: Annotated[int, Field(gt=0, le=1000)] = 3
    retry_base_backoff_seconds: float = 2.0
    retry_backoff_cap_seconds: float = 30.0

    # Demo/test-only failure injection (real "delivery" is just a log line and can't fail on its own): 0 disables it, N fails every attempt <= N.
    simulated_failure_attempts: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging() -> None:
    """Route both structlog calls and stdlib logging (incl. uvicorn's)
    through the same JSON renderer — mirrors every other service's
    core.py exactly."""
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


_kafka_producer: AIOKafkaProducer | None = None
_kafka_producer_lock = asyncio.Lock()


async def get_kafka_producer() -> AIOKafkaProducer:
    global _kafka_producer
    # Lock guards the await between the None-check and assignment, so two concurrent first callers can't both start a producer.
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
