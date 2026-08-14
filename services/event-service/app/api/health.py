from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import HealthResponse
from app.core import get_session
from app.logic.health_manager import HealthManager

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    """Public — no auth required."""
    if not await HealthManager(session).check():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unreachable")
    return HealthResponse(status="ok")
