from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import demo, health
from app.core import configure_logging, dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="event-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(demo.router)
    Instrumentator().instrument(app).expose(app)

    return app


app = create_app()
