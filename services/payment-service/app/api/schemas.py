import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.db.models import PaymentStatus

# ISO 4217 code, e.g. "usd" — rejects blank/whitespace-only strings.
Currency = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=3)]


class HealthResponse(BaseModel):
    status: str


class ChargeRequest(BaseModel):
    """Called by Booking Service only (§9 amendment — Booking Service fronts
    payment), never directly by a browser client. Booking Service already
    knows all four fields locally (the booking it just ownership-checked,
    the ticket's price_cents), so this endpoint does no lookup of its own."""

    booking_id: uuid.UUID
    ticket_id: uuid.UUID
    amount_cents: int = Field(gt=0)
    currency: Currency
    # No card-collection UI exists (§10) — real callers get the default always-succeeds test payment method; tests override it to simulate declines.
    payment_method: str = "pm_card_visa"


class PaymentResponse(BaseModel):
    id: uuid.UUID
    booking_id: uuid.UUID
    ticket_id: uuid.UUID
    amount_cents: int
    currency: str
    status: PaymentStatus
    created_at: datetime

    model_config = {"from_attributes": True}
