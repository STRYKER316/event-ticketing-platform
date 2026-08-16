import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import BookingStatus


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
