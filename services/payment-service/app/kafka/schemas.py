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
    # BOOKING_CONFIRMED is booking-service's own producer-side member (Phase 5), not sent from here.
    PAYMENT_CONFIRMED = "payment_confirmed"
    REFUND_FAILED = "refund_failed"


class NotificationMessage(BaseModel):
    """Notification Service's own consumer now exists (Phase 5) — this
    was producer-side only through Phase 6 (§22 amendment #3)."""

    action: NotificationAction
    booking_id: uuid.UUID
    # PAYMENT_CONFIRMED has no natural reason text — only REFUND_FAILED supplies one (the Stripe error message).
    reason: str | None = None
