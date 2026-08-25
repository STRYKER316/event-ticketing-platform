import asyncio
import contextlib
import socket
from contextlib import asynccontextmanager

import shared_auth
import structlog
from aiokafka import AIOKafkaConsumer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from redis.exceptions import RedisError

from app.api import bookings, health
from app.core import (
    close_http_client,
    close_kafka_producer,
    close_redis,
    configure_logging,
    dispose_engine,
    get_redis,
    get_session_factory,
)
from app.kafka.consumers import (
    PaymentOutcomeConsumer,
    ProvisioningConsumer,
    build_kafka_consumer,
    build_payment_outcome_consumer,
)
from app.kafka.producers import get_booking_cancelled_producer, get_notification_producer
from app.logic.helpers.hold_sweep import build_scheduler

logger = structlog.get_logger()


def _log_if_died(name: str, task: asyncio.Task) -> None:
    # A task's exception is otherwise only surfaced on garbage-collection, which never happens while `lifespan` holds a live reference — a crash would stay silent without this.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical(f"{name}_task_died", error=str(exc), exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    kafka_consumer: AIOKafkaConsumer | None = None
    consumer_task: asyncio.Task | None = None
    payment_outcome_kafka_consumer: AIOKafkaConsumer | None = None
    payment_outcome_task: asyncio.Task | None = None
    # Both hold strategies need a periodic sweep (build_scheduler() picks the right job, §6); an unrecognized HOLD_STRATEGY fails here same as on the first booking request.
    scheduler: AsyncIOScheduler = build_scheduler()
    try:
        kafka_consumer = build_kafka_consumer()
        await kafka_consumer.start()
        consumer_task = asyncio.create_task(ProvisioningConsumer(kafka_consumer, get_session_factory()).run())
        consumer_task.add_done_callback(lambda task: _log_if_died("provisioning_consumer", task))

        payment_outcome_kafka_consumer = build_payment_outcome_consumer()
        await payment_outcome_kafka_consumer.start()
        payment_outcome_task = asyncio.create_task(
            PaymentOutcomeConsumer(
                payment_outcome_kafka_consumer,
                get_session_factory(),
                await get_redis(),
                await get_notification_producer(),
                await get_booking_cancelled_producer(),
            ).run()
        )
        payment_outcome_task.add_done_callback(lambda task: _log_if_died("payment_outcome_consumer", task))

        scheduler.start()
        yield
    finally:
        # try/finally so a startup failure still closes whatever was already opened, instead of leaking the consumer.
        # scheduler.start() may never have run, and shutdown() on a never-started scheduler raises SchedulerNotRunningError, masking the original exception — hence the running check.
        if scheduler.running:
            scheduler.shutdown(wait=False)
        for task in (consumer_task, payment_outcome_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for consumer in (kafka_consumer, payment_outcome_kafka_consumer):
            if consumer is not None:
                await consumer.stop()
        # Independent teardowns — no ordering dependency between them.
        await asyncio.gather(
            dispose_engine(), close_redis(), close_http_client(), close_kafka_producer(), shared_auth.aclose()
        )


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="booking-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(bookings.router)
    Instrumentator().instrument(app).expose(app)

    @app.exception_handler(RedisError)
    async def redis_error_handler(request: Request, exc: RedisError) -> JSONResponse:
        # TicketHoldStrategy's bool-returning interface can't signal "backend unreachable" without conflating it with "seat unavailable", so it's caught here at the ASGI boundary for one clean 503 across every HTTP route instead of a try/except per route.
        # Kafka consumers are unaffected — this only wraps the HTTP cycle, so PaymentOutcomeConsumer's _run_with_retry still sees the raw exception and retries as designed.
        logger.error("hold_backend_unreachable", error=str(exc))
        return JSONResponse(status_code=503, content={"detail": "hold service unavailable"})

    async def database_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
        # Postgres connect failures surface as raw ConnectionError/gaierror/TimeoutError — SQLAlchemy's pool checkout only wraps execute-time DBAPI errors, not connect-time ones.
        logger.error("database_unavailable", error=str(exc))
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})

    for exc_class in (ConnectionError, socket.gaierror, TimeoutError):
        app.add_exception_handler(exc_class, database_unavailable_handler)

    return app


app = create_app()
