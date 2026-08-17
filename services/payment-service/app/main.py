from contextlib import asynccontextmanager

import shared_auth
import stripe
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import health, payments
from app.core import close_kafka_producer, configure_logging, dispose_engine, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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
