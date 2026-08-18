import enum
import uuid

from pydantic import BaseModel


class NotificationAction(enum.Enum):
    """The one place that has to recognize every producer's action —
    booking-service (BOOKING_CONFIRMED) and payment-service
    (PAYMENT_CONFIRMED, REFUND_FAILED) each publish to the shared
    notifications topic with their own independently-defined, narrower
    copy of this enum (§7 point 3)."""

    BOOKING_CONFIRMED = "booking_confirmed"
    PAYMENT_CONFIRMED = "payment_confirmed"
    REFUND_FAILED = "refund_failed"


class NotificationMessage(BaseModel):
    action: NotificationAction
    booking_id: uuid.UUID
    reason: str | None = None


class RetryEnvelope(BaseModel):
    """Carries retry state on the message itself (decisions-log §17
    amendment, 2026-08-18) — this service has no DB to hold it in.
    `attempt` is the next attempt number about to be made: 2 for the first
    retry, since the initial delivery off `notifications` is attempt 1."""

    attempt: int
    original: NotificationMessage
    last_error: str
