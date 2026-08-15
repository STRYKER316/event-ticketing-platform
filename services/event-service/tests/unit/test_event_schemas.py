import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.api.schemas import EventCreate, EventUpdate, Seat, SeatMapRow, SeatMapSection, SeatMapUpsert, VenueCreate

FUTURE = datetime.now(timezone.utc) + timedelta(days=1)


def test_create_rejects_naive_start_time_instead_of_crashing():
    with pytest.raises(ValidationError) as exc_info:
        EventCreate(
            title="t",
            start_time="2027-01-01T10:00:00",
            end_time="2027-01-01T12:00:00",
            venue_id=uuid.uuid4(),
        )
    assert exc_info.value.errors()[0]["type"] == "timezone_aware"


def test_update_rejects_naive_end_time_instead_of_crashing():
    with pytest.raises(ValidationError) as exc_info:
        EventUpdate(end_time="2027-01-01T12:00:00")
    assert exc_info.value.errors()[0]["type"] == "timezone_aware"


def test_create_accepts_aware_datetimes():
    event = EventCreate(
        title="t",
        start_time=FUTURE,
        end_time=FUTURE + timedelta(hours=2),
        venue_id=uuid.uuid4(),
    )
    assert event.start_time == FUTURE


def test_venue_create_rejects_non_positive_capacity():
    with pytest.raises(ValidationError) as exc_info:
        VenueCreate(name="Arena", address="1 Main St", capacity=0)
    assert "capacity must be positive" in str(exc_info.value)


def test_venue_create_rejects_blank_name():
    with pytest.raises(ValidationError):
        VenueCreate(name="   ", address="1 Main St", capacity=100)


def test_venue_create_accepts_valid_payload():
    venue = VenueCreate(name="Arena", address="1 Main St", capacity=100)
    assert venue.capacity == 100


def test_seat_map_upsert_rejects_empty_sections():
    with pytest.raises(ValidationError):
        SeatMapUpsert(sections=[])


def test_seat_map_upsert_accepts_valid_payload():
    upsert = SeatMapUpsert(
        sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
    )
    assert len(upsert.sections) == 1
