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


def test_seat_map_upsert_rejects_a_section_with_no_rows():
    # A non-empty `sections` list satisfies SeatMapUpsert's own constraint
    # while still describing zero actual seats if nothing stops an empty
    # `rows` list nested inside it.
    with pytest.raises(ValidationError):
        SeatMapUpsert(sections=[SeatMapSection(name="A", rows=[])])


def test_seat_map_upsert_rejects_a_row_with_no_seats():
    with pytest.raises(ValidationError):
        SeatMapUpsert(sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[])])])


def test_seat_map_upsert_accepts_valid_payload():
    upsert = SeatMapUpsert(
        sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
    )
    assert len(upsert.sections) == 1


def test_venue_create_rejects_name_over_the_db_column_limit():
    # events.name is varchar(255) -- an over-length value must be a clean 422 at the
    # DTO boundary, not an unhandled asyncpg.StringDataRightTruncationError at commit.
    with pytest.raises(ValidationError):
        VenueCreate(name="A" * 256, address="1 Main St", capacity=100)


def test_venue_create_rejects_address_over_the_db_column_limit():
    with pytest.raises(ValidationError):
        VenueCreate(name="Arena", address="A" * 501, capacity=100)


def test_venue_create_rejects_capacity_beyond_postgres_int4_range():
    with pytest.raises(ValidationError):
        VenueCreate(name="Arena", address="1 Main St", capacity=2_147_483_648)


def test_venue_create_accepts_capacity_at_postgres_int4_max():
    venue = VenueCreate(name="Arena", address="1 Main St", capacity=2_147_483_647)
    assert venue.capacity == 2_147_483_647


def test_venue_create_rejects_nul_byte_in_name():
    with pytest.raises(ValidationError):
        VenueCreate(name="Are\x00na", address="1 Main St", capacity=100)


def test_event_create_rejects_title_over_the_db_column_limit():
    # events.title is varchar(255), same failure mode as the venue name above.
    with pytest.raises(ValidationError):
        EventCreate(title="A" * 256, start_time=FUTURE, end_time=FUTURE + timedelta(hours=2), venue_id=uuid.uuid4())


def test_event_create_rejects_description_over_the_db_column_limit():
    with pytest.raises(ValidationError):
        EventCreate(
            title="t",
            description="A" * 5001,
            start_time=FUTURE,
            end_time=FUTURE + timedelta(hours=2),
            venue_id=uuid.uuid4(),
        )


def test_event_create_rejects_nul_byte_in_title():
    with pytest.raises(ValidationError):
        EventCreate(title="ti\x00tle", start_time=FUTURE, end_time=FUTURE + timedelta(hours=2), venue_id=uuid.uuid4())
