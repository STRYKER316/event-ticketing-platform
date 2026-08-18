from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health
from app.core import close_kafka_producer, configure_logging

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No shared_auth.aclose() here (unlike every other service's lifespan)
    # — this service exposes no protected routes, so no JWKS client is ever
    # created (see this phase's kickoff doc process note).
    yield
    await close_kafka_producer()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="notification-service", lifespan=lifespan)
    app.include_router(health.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
