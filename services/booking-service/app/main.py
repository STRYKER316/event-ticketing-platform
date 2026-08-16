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
from app.core import close_redis, configure_logging, dispose_engine, get_session_factory
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
    # Both hold strategies need a periodic sweep (build_scheduler() picks the
    # right job for whichever is active, §6) — an unrecognized HOLD_STRATEGY
    # fails here the same way it would on the first booking request.
    scheduler: AsyncIOScheduler = build_scheduler()
    try:
        kafka_consumer = build_kafka_consumer()
        await kafka_consumer.start()
        consumer_task = asyncio.create_task(ProvisioningConsumer(kafka_consumer, get_session_factory()).run())
        consumer_task.add_done_callback(_log_if_died)
        scheduler.start()
        yield
    finally:
        # try/finally so a failure partway through startup still closes
        # whatever was already opened, instead of leaking the consumer.
        # scheduler.start() (above) may never have run — e.g. kafka_consumer
        # .start() raised first — and shutdown() on a never-started scheduler
        # raises SchedulerNotRunningError, which would mask the original
        # exception and abort every teardown step below it.
        if scheduler.running:
            scheduler.shutdown(wait=False)
        if consumer_task is not None:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
        if kafka_consumer is not None:
            await kafka_consumer.stop()
        # Independent teardowns — no ordering dependency between them.
        await asyncio.gather(dispose_engine(), close_redis(), shared_auth.aclose())


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="booking-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(bookings.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
