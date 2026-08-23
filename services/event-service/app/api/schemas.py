import enum
import uuid
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, StringConstraints, field_validator, model_validator

from app.db.models import EventStatus


def _reject_nul_bytes(value: str) -> str:
    # Postgres rejects embedded NUL bytes; catch here for a clean 422, not a 500.
    if "\x00" in value:
        raise ValueError("must not contain NUL bytes")
    return value


_NoNulBytes = AfterValidator(_reject_nul_bytes)

POSTGRES_INT4_MAX = 2_147_483_647  # Postgres Integer column max


# Length caps mirror models.py's DB column limits (StringDataRightTruncationError otherwise).
VenueName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255), _NoNulBytes]
VenueAddress = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500), _NoNulBytes]
EventTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255), _NoNulBytes]
EventDescription = Annotated[str, StringConstraints(max_length=5000), _NoNulBytes]  # no strip/min: stays optional/permissive

# Keeps one seat map's Kafka message under aiokafka's 1MB max_request_size.
MAX_SEAT_MAP_SEATS = 20_000


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
    name: VenueName
    address: VenueAddress
    capacity: int = Field(le=POSTGRES_INT4_MAX)

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
    title: EventTitle
    description: EventDescription | None = None
    start_time: AwareDatetime
    end_time: AwareDatetime
    venue_id: uuid.UUID
    performer_ids: list[uuid.UUID] = Field(default=[], max_length=1000)  # asyncpg's IN() bind-param cap is 32767

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
    title: EventTitle | None = None
    description: EventDescription | None = None
    start_time: AwareDatetime | None = None
    end_time: AwareDatetime | None = None
    venue_id: uuid.UUID | None = None
    performer_ids: Annotated[list[uuid.UUID], Field(max_length=1000)] | None = None

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
    label: str = Field(min_length=1, max_length=100)
    x: float = Field(allow_inf_nan=False)  # NaN/Infinity aren't valid JSON; reject rather than silently store
    y: float = Field(allow_inf_nan=False)


class SeatMapRow(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    seats: list[Seat] = Field(min_length=1)


class SeatMapSection(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rows: list[SeatMapRow] = Field(min_length=1)
    # Organizer-set per section (§9/§16), e.g. floor vs. balcony — carried via Kafka into Booking Service's Ticket.price_cents.
    price_cents: int = Field(gt=0, le=POSTGRES_INT4_MAX)


class SeatMap(BaseModel):
    event_id: uuid.UUID
    sections: list[SeatMapSection]


class SeatMapUpsert(BaseModel):
    sections: list[SeatMapSection] = Field(min_length=1)

    @model_validator(mode="after")
    def total_seats_within_limit(self) -> "SeatMapUpsert":
        total = sum(len(row.seats) for section in self.sections for row in section.rows)
        if total > MAX_SEAT_MAP_SEATS:
            raise ValueError(f"seat map has {total} seats, exceeding the {MAX_SEAT_MAP_SEATS} limit")
        return self

    @model_validator(mode="after")
    def no_duplicate_seats(self) -> "SeatMapUpsert":
        # booking-service's unique constraint silently drops duplicates rather than erroring, so catch here or seat count silently falls short.
        seen: set[tuple[str, str, str]] = set()
        for section in self.sections:
            for row in section.rows:
                for seat in row.seats:
                    key = (section.name, row.name, seat.label)
                    if key in seen:
                        raise ValueError(f"duplicate seat: section {section.name!r}, row {row.name!r}, label {seat.label!r}")
                    seen.add(key)
        return self
