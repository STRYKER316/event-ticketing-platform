import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.api.schemas import EventCreate, EventUpdate

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
