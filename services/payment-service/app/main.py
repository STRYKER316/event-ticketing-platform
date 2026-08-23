import asyncio
import contextlib
from contextlib import asynccontextmanager

import shared_auth
import stripe
import structlog
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health, payments
from app.core import close_kafka_producer, configure_logging, dispose_engine, get_session_factory, get_settings
from app.kafka.consumers import BookingCancelledConsumer, build_cancelled_bookings_consumer

logger = structlog.get_logger()


def _log_if_died(name: str, task: asyncio.Task) -> None:
    # Same reasoning as booking-service's own helper — otherwise a background task's exception stays silent until garbage-collected.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical(f"{name}_task_died", error=str(exc), exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    kafka_consumer: AIOKafkaConsumer | None = None
    consumer_task: asyncio.Task | None = None
    try:
        kafka_consumer = build_cancelled_bookings_consumer()
        await kafka_consumer.start()
        consumer_task = asyncio.create_task(
            BookingCancelledConsumer(kafka_consumer, get_session_factory()).run()
        )
        consumer_task.add_done_callback(lambda task: _log_if_died("booking_cancelled_consumer", task))

        yield
    finally:
        if consumer_task is not None:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
        if kafka_consumer is not None:
            await kafka_consumer.stop()
        await dispose_engine()
        await close_kafka_producer()
        await shared_auth.aclose()


def create_app() -> FastAPI:
    configure_logging()
    stripe.api_key = get_settings().stripe_secret_key

    app = FastAPI(title="payment-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(payments.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
