import asyncio
import contextlib
from contextlib import asynccontextmanager

import structlog
from aiokafka import AIOKafkaConsumer
from elastic_transport import TransportError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health, search
from app.core import close_es_client, configure_logging, get_es_client
from app.db.event_index_repository import EventIndexRepository
from app.kafka.consumers import EventConsumer, build_kafka_consumer

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = EventIndexRepository(get_es_client())
    kafka_consumer: AIOKafkaConsumer | None = None
    consumer_task: asyncio.Task | None = None
    try:
        await repository.ensure_index()
        kafka_consumer = build_kafka_consumer()
        await kafka_consumer.start()
        consumer_task = asyncio.create_task(EventConsumer(kafka_consumer, repository).run())
        yield
    finally:
        # try/finally so a startup failure (e.g. ensure_index() timing out) still closes whatever was already opened.
        if consumer_task is not None:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
        if kafka_consumer is not None:
            await kafka_consumer.stop()
        await close_es_client()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="search-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(search.router)
    Instrumentator().instrument(app).expose(app)

    @app.exception_handler(TransportError)
    async def es_transport_error_handler(request: Request, exc: TransportError) -> JSONResponse:
        # Same reasoning as booking-service's RedisError handler — covers HTTP routes only; EventConsumer's Kafka-side writes retry separately via _run_with_retry.
        logger.error("search_backend_unreachable", error=str(exc))
        return JSONResponse(status_code=503, content={"detail": "search backend unavailable"})

    return app


app = create_app()
