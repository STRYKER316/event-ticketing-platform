from fastapi import APIRouter, Depends
from shared_auth import Principal, get_current_user, require_role

from app.api.schemas import DemoResponse

router = APIRouter(prefix="/demo")


@router.get("/protected", response_model=DemoResponse)
async def protected(user: Principal = Depends(get_current_user)) -> DemoResponse:
    """Authenticated-only — any valid role."""
    return DemoResponse(message="authenticated", username=user.username, roles=user.roles)


@router.get("/organizer-only", response_model=DemoResponse)
async def organizer_only(user: Principal = Depends(require_role("organizer"))) -> DemoResponse:
    """Ownership-scoped placeholder — role check only, no resource yet to own."""
    return DemoResponse(message="organizer access granted", username=user.username, roles=user.roles)
