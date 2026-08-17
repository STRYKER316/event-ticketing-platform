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
    price_cents: int


class EventUpsertedMessage(BaseModel):
    """Event-carried state transfer (§7.2): the full seat list travels with the
    event so downstream consumers never need a synchronous callback into
    Event Service."""

    action: KafkaAction = KafkaAction.UPSERTED
    event_id: uuid.UUID
    title: str
    description: str | None
    start_time: datetime
    end_time: datetime
    venue_name: str
    performer_names: list[str]
    seats: list[EventSeat]
