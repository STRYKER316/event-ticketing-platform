from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas import HealthResponse
from app.core import get_es_client
from app.logic.health_manager import HealthManager

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz(client: AsyncElasticsearch = Depends(get_es_client)) -> HealthResponse:
    """Public — no auth required."""
    if not await HealthManager(client).check():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "elasticsearch unreachable")
    return HealthResponse(status="ok")
