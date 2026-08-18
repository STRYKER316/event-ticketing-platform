import asyncio
import contextlib
from contextlib import asynccontextmanager

import structlog
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health
from app.core import close_kafka_producer, configure_logging
from app.kafka.consumers import NotificationConsumer, build_notification_consumer

logger = structlog.get_logger()


def _log_if_died(name: str, task: asyncio.Task) -> None:
    # Same reasoning as booking-service/payment-service's own helper — a
    # background task's exception is otherwise only surfaced when the task
    # object is garbage-collected, which never happens while `lifespan`
    # holds a live reference to it for the app's whole lifetime.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical(f"{name}_task_died", error=str(exc), exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No shared_auth.aclose() here (unlike every other service's lifespan)
    # — this service exposes no protected routes, so no JWKS client is ever
    # created (see this phase's kickoff doc process note).
    kafka_consumer: AIOKafkaConsumer | None = None
    consumer_task: asyncio.Task | None = None
    try:
        kafka_consumer = build_notification_consumer()
        await kafka_consumer.start()
        consumer_task = asyncio.create_task(NotificationConsumer(kafka_consumer).run())
        consumer_task.add_done_callback(lambda task: _log_if_died("notification_consumer", task))

        yield
    finally:
        if consumer_task is not None:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
        if kafka_consumer is not None:
            await kafka_consumer.stop()
        await close_kafka_producer()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="notification-service", lifespan=lifespan)
    app.include_router(health.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
