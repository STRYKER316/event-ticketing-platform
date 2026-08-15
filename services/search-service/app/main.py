import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health, search
from app.core import close_es_client, configure_logging, get_es_client
from app.db.event_index_repository import EventIndexRepository
from app.kafka.consumers import EventConsumer, build_kafka_consumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = EventIndexRepository(get_es_client())
    await repository.ensure_index()

    kafka_consumer = build_kafka_consumer()
    await kafka_consumer.start()
    consumer_task = asyncio.create_task(EventConsumer(kafka_consumer, repository).run())

    yield

    consumer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer_task
    await kafka_consumer.stop()
    await close_es_client()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="search-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(search.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
