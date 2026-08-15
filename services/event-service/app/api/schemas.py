import enum
import uuid
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, field_validator, model_validator

from app.db.models import EventStatus

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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


class VenueCreate(BaseModel):
    name: NonBlankStr
    address: NonBlankStr
    capacity: int

    @field_validator("capacity")
    @classmethod
    def capacity_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("capacity must be positive")
        return value


class PerformerResponse(BaseModel):
    id: uuid.UUID
    name: str
    bio: str | None

    model_config = {"from_attributes": True}


class EventResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    start_time: AwareDatetime
    end_time: AwareDatetime
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


class EventCreate(BaseModel):
    title: NonBlankStr
    description: str | None = None
    start_time: AwareDatetime
    end_time: AwareDatetime
    venue_id: uuid.UUID
    performer_ids: list[uuid.UUID] = []

    @field_validator("start_time")
    @classmethod
    def start_time_not_in_past(cls, value: AwareDatetime) -> AwareDatetime:
        if value <= datetime.now(timezone.utc):
            raise ValueError("start_time must be in the future")
        return value

    @model_validator(mode="after")
    def end_after_start(self) -> "EventCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class EventUpdate(BaseModel):
    title: NonBlankStr | None = None
    description: str | None = None
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    venue_id: uuid.UUID | None = None
    performer_ids: list[uuid.UUID] | None = None

    @field_validator("start_time")
    @classmethod
    def start_time_not_in_past(cls, value: AwareDatetime | None) -> AwareDatetime | None:
        if value is not None and value <= datetime.now(timezone.utc):
            raise ValueError("start_time must be in the future")
        return value

    @model_validator(mode="after")
    def end_after_start(self) -> "EventUpdate":
        if self.start_time is not None and self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


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
    event_id: uuid.UUID
    sections: list[SeatMapSection]


class SeatMapUpsert(BaseModel):
    sections: list[SeatMapSection] = Field(min_length=1)
