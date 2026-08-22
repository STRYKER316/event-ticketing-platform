import enum
import uuid
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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
    retry, since the initial delivery off `notifications` is attempt 1.
    Bounded well above any realistic retry_max_attempts config — this
    envelope round-trips through Kafka, so a malformed or tampered message
    is untrusted input at the DTO boundary, not just an internal counter —
    an unbounded attempt could overflow compute_backoff_seconds's
    exponentiation. RetryConsumer constructs the
    next envelope as `attempt + 1`, so the bound leaves headroom above any
    sane retry_max_attempts config rather than sitting flush against it."""

    attempt: Annotated[int, Field(ge=1, le=1000)]
    original: NotificationMessage
    last_error: NonBlankStr
