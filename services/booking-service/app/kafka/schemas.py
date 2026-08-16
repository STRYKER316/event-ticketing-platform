import enum
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

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


class EventUpsertedMessage(BaseModel):
    """Mirrors event-service's producer-side schema (§7.2 event-carried state
    transfer) — the two sides are independently defined, not shared code,
    since these are separate deployable services communicating over Kafka."""

    action: KafkaAction
    event_id: uuid.UUID
    title: NonBlankStr
    description: str | None
    start_time: datetime
    end_time: datetime
    venue_name: NonBlankStr
    performer_names: list[str]
    seats: list[EventSeat]
