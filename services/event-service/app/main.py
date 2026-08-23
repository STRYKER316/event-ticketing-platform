import math
from contextlib import asynccontextmanager
from typing import Any

import shared_auth
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import events, health
from app.core import close_kafka_producer, close_mongo_client, configure_logging, dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispose_engine()
    close_mongo_client()
    await close_kafka_producer()
    await shared_auth.aclose()


def _json_safe(value: Any) -> Any:
    # Starlette's JSONResponse rejects NaN/Infinity; echoing a rejected value back in the 422 body would otherwise crash it into a 500.
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="event-service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(events.router)
    Instrumentator().instrument(app).expose(app)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_json_safe(jsonable_encoder({"detail": exc.errors()})))

    return app


app = create_app()
