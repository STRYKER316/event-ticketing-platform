import stripe
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from shared_auth import Principal, get_current_user
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChargeRequest, PaymentResponse
from app.core import get_session, get_settings
from app.db.payment_repository import PaymentRepository
from app.kafka.producers import NotificationProducer, PaymentOutcomeProducer, get_notification_producer, get_payment_outcome_producer
from app.logic.payment_manager import PaymentManager

logger = structlog.get_logger()

router = APIRouter()


@router.post("/payments/charge", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_charge(
    payload: ChargeRequest,
    _user: Principal = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentResponse:
    """Authenticated-only, no ownership check here: this endpoint is called
    by Booking Service, not directly by a browser client (decisions-log §9
    amendment — Booking Service fronts payment). Booking Service already
    verified the caller owns the booking before making this call; Payment
    Service only needs to know the caller presented a valid Keycloak token
    (any token — it forwards the caller's own bearer token unmodified, so
    this validates the same way regardless of which service presents it).
    A valid token alone is *not* sufficient to trust `amount_cents` — this
    route's real access control is that Traefik never exposes it publicly
    (`infra/docker-compose.yml`'s router rule covers only `/payments/webhook`,
    deliberately narrower than every other service's), so it's reachable
    only over the internal Docker network, by booking-service, which is
    the thing that actually enforces ownership and looks up the
    authoritative price — a valid JWT alone would otherwise let any user
    submit an arbitrary amount for any booking."""
    manager = PaymentManager(session=session, payments=PaymentRepository(session))
    return await manager.create_charge(payload)


@router.post("/payments/webhook")
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    producer: PaymentOutcomeProducer = Depends(get_payment_outcome_producer),
    notification_producer: NotificationProducer = Depends(get_notification_producer),
) -> dict:
    """Public — no JWT. Stripe's own webhook signature verification below
    *is* this route's auth requirement, not an oversight (per the
    auth-requirement convention): a real logged-in user never calls this
    route, Stripe's servers do."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, get_settings().stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning("webhook_signature_invalid", error=str(exc))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid webhook signature") from exc

    manager = PaymentManager(session=session, payments=PaymentRepository(session))
    await manager.handle_webhook_event(event, producer, notification_producer)
    return {"received": True}
