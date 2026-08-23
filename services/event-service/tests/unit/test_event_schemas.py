import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    MAX_SEAT_MAP_SEATS,
    POSTGRES_INT4_MAX,
    STRIPE_MIN_CHARGE_CENTS_USD,
    EventCreate,
    EventUpdate,
    Seat,
    SeatMapRow,
    SeatMapSection,
    SeatMapUpsert,
    VenueCreate,
)

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
    # A non-empty `sections` list still describes zero actual seats if nothing stops an empty `rows` list nested inside it.
    with pytest.raises(ValidationError):
        SeatMapUpsert(sections=[SeatMapSection(price_cents=2500, name="A", rows=[])])


def test_seat_map_upsert_rejects_a_row_with_no_seats():
    with pytest.raises(ValidationError):
        SeatMapUpsert(sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[])])])


def test_seat_map_upsert_accepts_valid_payload():
    upsert = SeatMapUpsert(
        sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
    )
    assert len(upsert.sections) == 1


def test_seat_map_section_rejects_non_positive_price():
    with pytest.raises(ValidationError):
        SeatMapSection(price_cents=0, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])


def test_seat_map_section_rejects_price_below_stripe_usd_minimum():
    with pytest.raises(ValidationError):
        SeatMapSection(
            price_cents=STRIPE_MIN_CHARGE_CENTS_USD - 1,
            name="A",
            rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])],
        )


def test_seat_map_section_accepts_price_at_stripe_usd_minimum():
    section = SeatMapSection(
        price_cents=STRIPE_MIN_CHARGE_CENTS_USD,
        name="A",
        rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])],
    )
    assert section.price_cents == STRIPE_MIN_CHARGE_CENTS_USD


def test_seat_map_section_rejects_price_beyond_postgres_int4_range():
    # An out-of-range price flows through Kafka into a plain Postgres int4 column and would otherwise permanently fail the insert on every redelivery.
    with pytest.raises(ValidationError):
        SeatMapSection(
            price_cents=POSTGRES_INT4_MAX + 1, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])]
        )


def test_seat_map_section_accepts_price_at_postgres_int4_max():
    section = SeatMapSection(
        price_cents=POSTGRES_INT4_MAX, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])]
    )
    assert section.price_cents == POSTGRES_INT4_MAX


def test_venue_create_rejects_name_over_the_db_column_limit():
    # events.name is varchar(255)
    with pytest.raises(ValidationError):
        VenueCreate(name="A" * 256, address="1 Main St", capacity=100)


def test_venue_create_rejects_address_over_the_db_column_limit():
    with pytest.raises(ValidationError):
        VenueCreate(name="Arena", address="A" * 501, capacity=100)


def test_venue_create_rejects_capacity_beyond_postgres_int4_range():
    with pytest.raises(ValidationError):
        VenueCreate(name="Arena", address="1 Main St", capacity=POSTGRES_INT4_MAX + 1)


def test_venue_create_accepts_capacity_at_postgres_int4_max():
    venue = VenueCreate(name="Arena", address="1 Main St", capacity=POSTGRES_INT4_MAX)
    assert venue.capacity == POSTGRES_INT4_MAX


def test_venue_create_rejects_nul_byte_in_name():
    with pytest.raises(ValidationError):
        VenueCreate(name="Are\x00na", address="1 Main St", capacity=100)


def test_event_create_rejects_title_over_the_db_column_limit():
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


def test_event_create_preserves_whitespace_in_description():
    event = EventCreate(
        title="t", description="   spaced   ", start_time=FUTURE, end_time=FUTURE + timedelta(hours=2), venue_id=uuid.uuid4()
    )
    assert event.description == "   spaced   "


def _seats(count: int) -> list[SeatMapSection]:
    return [SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label=f"S{i}", x=0, y=0) for i in range(count)])])]


def test_seat_map_upsert_rejects_more_seats_than_the_limit():
    with pytest.raises(ValidationError):
        SeatMapUpsert(sections=_seats(MAX_SEAT_MAP_SEATS + 1))


def test_seat_map_upsert_accepts_exactly_the_seat_limit():
    upsert = SeatMapUpsert(sections=_seats(MAX_SEAT_MAP_SEATS))
    assert sum(len(row.seats) for section in upsert.sections for row in section.rows) == MAX_SEAT_MAP_SEATS


def test_seat_map_upsert_rejects_a_seat_label_over_the_length_limit():
    with pytest.raises(ValidationError):
        SeatMapUpsert(sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="L" * 101, x=0, y=0)])])])


def test_seat_map_upsert_rejects_a_section_name_over_the_length_limit():
    with pytest.raises(ValidationError):
        SeatMapUpsert(
            sections=[SeatMapSection(price_cents=2500, name="A" * 101, rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
        )


def test_seat_map_upsert_rejects_a_row_name_over_the_length_limit():
    with pytest.raises(ValidationError):
        SeatMapUpsert(
            sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1" * 101, seats=[Seat(label="A1", x=0, y=0)])])]
        )


def test_seat_rejects_nan_x():
    with pytest.raises(ValidationError):
        Seat(label="A1", x=float("nan"), y=0)


def test_seat_rejects_infinity_y():
    with pytest.raises(ValidationError):
        Seat(label="A1", x=0, y=float("inf"))


def test_seat_map_upsert_rejects_a_duplicate_seat_label_within_one_row():
    with pytest.raises(ValidationError):
        SeatMapUpsert(
            sections=[
                SeatMapSection(
                    price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="1", x=0, y=0), Seat(label="1", x=1, y=0)])]
                )
            ]
        )


def test_seat_map_upsert_rejects_a_duplicate_seat_label_across_sections_sharing_a_row_name():
    with pytest.raises(ValidationError):
        SeatMapUpsert(
            sections=[
                SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="1", x=0, y=0)])]),
                SeatMapSection(price_cents=3000, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="1", x=5, y=5)])]),
            ]
        )


def test_seat_map_upsert_accepts_the_same_label_in_different_rows():
    upsert = SeatMapUpsert(
        sections=[
            SeatMapSection(
                price_cents=2500,
                name="A",
                rows=[SeatMapRow(name="1", seats=[Seat(label="1", x=0, y=0)]), SeatMapRow(name="2", seats=[Seat(label="1", x=0, y=1)])],
            )
        ]
    )
    assert sum(len(row.seats) for section in upsert.sections for row in section.rows) == 2


def test_event_create_rejects_too_many_performer_ids():
    with pytest.raises(ValidationError):
        EventCreate(
            title="t",
            start_time=FUTURE,
            end_time=FUTURE + timedelta(hours=2),
            venue_id=uuid.uuid4(),
            performer_ids=[uuid.uuid4() for _ in range(1001)],
        )


def test_event_create_accepts_performer_ids_at_the_limit():
    event = EventCreate(
        title="t",
        start_time=FUTURE,
        end_time=FUTURE + timedelta(hours=2),
        venue_id=uuid.uuid4(),
        performer_ids=[uuid.uuid4() for _ in range(1000)],
    )
    assert len(event.performer_ids) == 1000


def test_event_update_rejects_too_many_performer_ids():
    with pytest.raises(ValidationError):
        EventUpdate(performer_ids=[uuid.uuid4() for _ in range(1001)])
