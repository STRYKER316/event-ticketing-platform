import asyncio
import logging
import sys
from functools import lru_cache

import structlog
from aiokafka import AIOKafkaProducer
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    notification_service_port: int = 8005

    log_level: str = "INFO"

    kafka_bootstrap_servers: str = "localhost:9094"
    notifications_topic: str = "notifications"
    notification_consumer_group_id: str = "notification-service-notifications"
    # Kafka #3's own retry/DLQ topics (decisions-log §17 amendment,
    # 2026-08-18) — retry state travels on the message (RetryEnvelope),
    # not in a table, since this service deliberately has no DB.
    notification_retry_topic: str = "notification-retry"
    notification_retry_consumer_group_id: str = "notification-service-retry"
    notification_dlq_topic: str = "notification-dlq"
    notification_dlq_consumer_group_id: str = "notification-service-dlq"

    # §17 amendment #2 — RetryEnvelope.attempt starts at 2 for the first
    # retry (the initial delivery off `notifications` is attempt 1);
    # attempt > retry_max_attempts routes to notification-dlq instead of
    # retrying again. Backoff per attempt: min(retry_base_backoff_seconds
    # ** attempt, retry_backoff_cap_seconds).
    retry_max_attempts: int = 3
    retry_base_backoff_seconds: float = 2.0
    retry_backoff_cap_seconds: float = 30.0

    # §17 amendment #3 — a demo/test instrument, not a production knob.
    # Nothing in this system's real "delivery" (a structured log line, §19)
    # can fail on its own, so proving the retry ladder for real rather than
    # only under a mocked unit test needs an explicit, honest failure-
    # injection point. 0 (default) disables it entirely: NotificationManager
    # .deliver() never raises in normal operation. A positive value N fails
    # every delivery attempt while attempt <= N, so set it to a small value
    # (e.g. 1) to demonstrate retry-then-recovery, or to a value >=
    # retry_max_attempts + 1 to demonstrate a message reaching
    # notification-dlq.
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
    # Unlike a plain None-check, this has an `await` between the check and
    # the assignment, so two concurrent first callers can otherwise both
    # start a producer — the loser's connection is then never stopped.
    # Mirrors payment-service/booking-service's own core.py exactly.
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
