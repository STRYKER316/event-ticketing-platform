import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import EventStatus


class HealthResponse(BaseModel):
    status: str


class EventSortField(enum.Enum):
    START_TIME = "start_time"
    TITLE = "title"


class SortOrder(enum.Enum):
    ASC = "asc"
    DESC = "desc"


class VenueResponse(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    capacity: int

    model_config = {"from_attributes": True}


class PerformerResponse(BaseModel):
    id: uuid.UUID
    name: str
    bio: str | None

    model_config = {"from_attributes": True}


class EventResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    start_time: datetime
    end_time: datetime
    status: EventStatus
    organizer_id: str
    venue: VenueResponse
    performers: list[PerformerResponse]

    model_config = {"from_attributes": True}


class EventListResponse(BaseModel):
    items: list[EventResponse]
    total: int
    limit: int
    offset: int


class Seat(BaseModel):
    label: str = Field(min_length=1)
    x: float
    y: float


class SeatMapRow(BaseModel):
    name: str = Field(min_length=1)
    seats: list[Seat]


class SeatMapSection(BaseModel):
    name: str = Field(min_length=1)
    rows: list[SeatMapRow]


class SeatMap(BaseModel):
    event_id: str
    sections: list[SeatMapSection]
