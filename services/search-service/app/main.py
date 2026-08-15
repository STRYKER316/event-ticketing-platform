from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health
from app.core import close_es_client, configure_logging, get_es_client
from app.db.event_index_repository import EventIndexRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    await EventIndexRepository(get_es_client()).ensure_index()
    yield
    await close_es_client()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="search-service", lifespan=lifespan)
    app.include_router(health.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
