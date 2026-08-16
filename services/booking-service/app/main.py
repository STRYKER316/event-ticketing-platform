import asyncio
import contextlib
from contextlib import asynccontextmanager

import shared_auth
import structlog
from aiokafka import AIOKafkaConsumer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import bookings, health
from app.core import close_redis, configure_logging, dispose_engine, get_session_factory, get_settings
from app.kafka.consumers import ProvisioningConsumer, build_kafka_consumer
from app.logic.helpers.hold_sweep import build_scheduler

logger = structlog.get_logger()


def _log_if_died(task: asyncio.Task) -> None:
    # A background task's exception is otherwise only surfaced when the task
    # object is garbage-collected — which never happens while `lifespan`
    # holds a live reference to it for the app's whole lifetime, so a crash
    # here would stay completely silent without this.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical("provisioning_consumer_task_died", error=str(exc), exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    kafka_consumer: AIOKafkaConsumer | None = None
    consumer_task: asyncio.Task | None = None
    # Only the cron strategy needs a sweep — Redis expires its own keys, no
    # scheduler needed for that strategy (§6).
    scheduler: AsyncIOScheduler | None = build_scheduler() if get_settings().hold_strategy == "cron" else None
    try:
        kafka_consumer = build_kafka_consumer()
        await kafka_consumer.start()
        consumer_task = asyncio.create_task(ProvisioningConsumer(kafka_consumer, get_session_factory()).run())
        consumer_task.add_done_callback(_log_if_died)
        if scheduler is not None:
            scheduler.start()
        yield
    finally:
        # try/finally so a failure partway through startup still closes
        # whatever was already opened, instead of leaking the consumer.
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        if consumer_task is not None:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
        if kafka_consumer is not None:
            await kafka_consumer.stop()
        await dispose_engine()
        await close_redis()
        await shared_auth.aclose()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="booking-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(bookings.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
