from fastapi import APIRouter

from app.api.schemas import HealthResponse

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Public — no auth required. No datastore of any kind (this phase's
    resolved "no DB" decision, decisions-log §17 amendment) to check, unlike
    every other service's /healthz — always healthy once the process is up."""
    return HealthResponse(status="ok")
