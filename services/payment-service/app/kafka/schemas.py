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


class BookingCancelledMessage(BaseModel):
    """Consumer-side schema for integration point #5 (§22) — mirrors
    booking-service's producer-side schema, independently defined on this
    side same as PaymentOutcomeMessage's own pattern is mirrored the other
    direction in booking-service/app/kafka/schemas.py."""

    booking_id: uuid.UUID


class NotificationAction(enum.Enum):
    # Only REFUND_FAILED exists this phase — BOOKING_CONFIRMED/
    # PAYMENT_CONFIRMED are Phase 5's job, added alongside their own
    # producer call sites and the consumer that will finally read all
    # three (§22 amendment #3).
    REFUND_FAILED = "refund_failed"


class NotificationMessage(BaseModel):
    """Producer side only this phase (§22 amendment #3) — integration
    point #3's consumer (Notification Service) doesn't exist until Phase
    5."""

    action: NotificationAction
    booking_id: uuid.UUID
    reason: str
