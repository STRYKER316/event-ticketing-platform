import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import PaymentStatus


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
    currency: str = Field(min_length=3, max_length=3)
    # No card-collection UI exists in this project's scope (§10) — Booking
    # Service never sets this, so every real caller gets the default
    # always-succeeds Stripe test payment method. The test suite overrides it
    # with a decline-simulating test payment method to exercise the failure
    # path against a real booking, rather than an unrelated `stripe trigger`
    # event with no booking attached.
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
