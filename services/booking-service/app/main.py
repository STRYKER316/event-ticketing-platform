from contextlib import asynccontextmanager

import shared_auth
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health
from app.core import close_redis, configure_logging, dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispose_engine()
    await close_redis()
    await shared_auth.aclose()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="booking-service", lifespan=lifespan)
    app.include_router(health.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
