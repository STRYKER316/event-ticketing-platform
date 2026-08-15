import enum
import uuid
from datetime import datetime

from pydantic import BaseModel


class KafkaAction(enum.Enum):
    UPSERTED = "upserted"
    DELETED = "deleted"


class EventSeat(BaseModel):
    section: str
    row: str
    label: str


class EventUpsertedMessage(BaseModel):
    """Mirrors event-service's producer-side schema (§7.2 event-carried state
    transfer) — the two sides are independently defined, not shared code,
    since these are separate deployable services communicating over Kafka."""

    action: KafkaAction
    event_id: uuid.UUID
    title: str
    description: str | None
    start_time: datetime
    end_time: datetime
    venue_name: str
    performer_names: list[str]
    seats: list[EventSeat]


class EventDeletedMessage(BaseModel):
    action: KafkaAction
    event_id: uuid.UUID
