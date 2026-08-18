import enum
import uuid
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

# This is the DTO validation boundary for this service (CLAUDE.md's "DTO
# layer is a strict validation boundary" rule applies to a Kafka consumer's
# payload the same as an API request body) — event-service's own producer-
# side DTOs already reject blank strings before a message is ever sent, but
# that's a different service's boundary, not this one's. A blank
# section/row/label would otherwise become a permanent, meaningless Ticket
# row via bulk_upsert_available().
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class KafkaAction(enum.Enum):
    UPSERTED = "upserted"
    DELETED = "deleted"


class EventSeat(BaseModel):
    section: NonBlankStr
    row: NonBlankStr
    label: NonBlankStr
    # Organizer-set per section (decisions-log §9/§16 amendments, 2026-08-17).
    price_cents: Annotated[int, Field(gt=0)]


class EventUpsertedMessage(BaseModel):
    """Mirrors event-service's producer-side schema (§7.2 event-carried state
    transfer) — the two sides are independently defined, not shared code,
    since these are separate deployable services communicating over Kafka."""

    action: KafkaAction
    event_id: uuid.UUID
    title: NonBlankStr
    description: str | None
    # AwareDatetime, not bare datetime (found in code review): start_time is
    # now load-bearing for the cancellation cutoff (§22 amendment #2),
    # compared against datetime.now(timezone.utc) — a naive value would
    # crash that comparison rather than silently misbehave, but rejecting
    # it at the DTO boundary is still the right place per the DTO-layer
    # convention, not a downstream check several calls deep.
    start_time: AwareDatetime
    end_time: AwareDatetime
    venue_name: NonBlankStr
    performer_names: list[str]
    seats: list[EventSeat]


class PaymentOutcomeAction(enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PaymentOutcomeMessage(BaseModel):
    """Mirrors payment-service's producer-side schema — integration point #4
    (decisions-log §7, broadened 2026-08-17), independently defined on this
    side same as EventUpsertedMessage above."""

    action: PaymentOutcomeAction
    booking_id: uuid.UUID
    ticket_id: uuid.UUID


class BookingCancelledMessage(BaseModel):
    """Producer-side schema for integration point #5 (§22) — this
    service's first-ever Kafka message. Payment Service already holds
    everything else it needs (amount, Stripe charge ID) keyed off this ID
    in its own payment_db."""

    booking_id: uuid.UUID


class NotificationAction(enum.Enum):
    # Only BOOKING_CONFIRMED exists on this service's side — PAYMENT_CONFIRMED
    # and REFUND_FAILED are payment-service's own producer-side members
    # (Phase 5/§7 point 3), not sent from here.
    BOOKING_CONFIRMED = "booking_confirmed"


class NotificationMessage(BaseModel):
    """Producer-side schema for integration point #3 (§7 point 3, Phase 5)
    — independently defined here, same pattern as every other mirrored
    schema in this file. Shares the notifications topic with payment-
    service's own NotificationMessage (app/kafka/schemas.py there), which
    has already been publishing to it since P6.T3."""

    action: NotificationAction
    booking_id: uuid.UUID
    reason: str | None = None
