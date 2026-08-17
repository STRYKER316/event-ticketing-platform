import enum
import uuid

from pydantic import BaseModel


class PaymentOutcomeAction(enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PaymentOutcomeMessage(BaseModel):
    """Integration point #4 (decisions-log §7, broadened 2026-08-17): one
    topic carries both outcomes, mirroring the UPSERTED/DELETED action
    pattern event-service's own producer already uses on event.events."""

    action: PaymentOutcomeAction
    booking_id: uuid.UUID
    ticket_id: uuid.UUID
