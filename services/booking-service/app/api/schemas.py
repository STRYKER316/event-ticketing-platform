import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import BookingStatus, TicketStatus


class HealthResponse(BaseModel):
    status: str


class BookingCreate(BaseModel):
    ticket_id: uuid.UUID


class BookingResponse(BaseModel):
    id: uuid.UUID
    user_subject: str
    event_id: uuid.UUID
    ticket_id: uuid.UUID
    status: BookingStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketStatusResponse(BaseModel):
    """Built explicitly from a Ticket row in BookingManager, not via
    from_attributes — the field is named ticket_id here (clearer for a
    frontend joining it against a seat map) but id on the model, so
    automatic attribute mapping doesn't apply."""

    ticket_id: uuid.UUID
    section: str
    row_name: str
    seat_label: str
    status: TicketStatus
    price_cents: int


class BookingPayResponse(BaseModel):
    """Proxies Payment Service's charge response — not a from_attributes
    model, since it's built from the synchronous call's JSON body, not a
    Booking Service model instance."""

    payment_id: uuid.UUID
    status: str
    amount_cents: int
    currency: str
