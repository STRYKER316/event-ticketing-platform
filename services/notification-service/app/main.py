import asyncio
import contextlib
from contextlib import asynccontextmanager

import structlog
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health
from app.core import close_kafka_producer, configure_logging
from app.kafka.consumers import (
    DlqConsumer,
    NotificationConsumer,
    RetryConsumer,
    build_dlq_consumer,
    build_notification_consumer,
    build_retry_consumer,
)
from app.kafka.producers import get_retry_publisher

logger = structlog.get_logger()


def _log_if_died(name: str, task: asyncio.Task) -> None:
    # Surfaces the exception now, since it otherwise only shows on GC, which never happens while `lifespan` keeps a live reference.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical(f"{name}_task_died", error=str(exc), exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No shared_auth.aclose() here — this service exposes no protected routes, so no JWKS client is ever created.
    notification_kafka_consumer: AIOKafkaConsumer | None = None
    notification_task: asyncio.Task | None = None
    retry_kafka_consumer: AIOKafkaConsumer | None = None
    retry_task: asyncio.Task | None = None
    dlq_kafka_consumer: AIOKafkaConsumer | None = None
    dlq_task: asyncio.Task | None = None
    try:
        retry_publisher = await get_retry_publisher()

        notification_kafka_consumer = build_notification_consumer()
        await notification_kafka_consumer.start()
        notification_task = asyncio.create_task(
            NotificationConsumer(notification_kafka_consumer, retry_publisher).run()
        )
        notification_task.add_done_callback(lambda task: _log_if_died("notification_consumer", task))

        retry_kafka_consumer = build_retry_consumer()
        await retry_kafka_consumer.start()
        retry_task = asyncio.create_task(RetryConsumer(retry_kafka_consumer, retry_publisher).run())
        retry_task.add_done_callback(lambda task: _log_if_died("retry_consumer", task))

        dlq_kafka_consumer = build_dlq_consumer()
        await dlq_kafka_consumer.start()
        dlq_task = asyncio.create_task(DlqConsumer(dlq_kafka_consumer).run())
        dlq_task.add_done_callback(lambda task: _log_if_died("dlq_consumer", task))

        yield
    finally:
        for task in (notification_task, retry_task, dlq_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for consumer in (notification_kafka_consumer, retry_kafka_consumer, dlq_kafka_consumer):
            if consumer is not None:
                await consumer.stop()
        await close_kafka_producer()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="notification-service", lifespan=lifespan)
    app.include_router(health.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
