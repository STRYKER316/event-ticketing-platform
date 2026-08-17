import enum
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


class KafkaAction(enum.Enum):
    UPSERTED = "upserted"
    DELETED = "deleted"


class EventSeat(BaseModel):
    section: str
    row: str
    label: str
    # Mirrors booking-service's consumer-side constraint (§9/§16 amendments).
    price_cents: Annotated[int, Field(gt=0)]


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
