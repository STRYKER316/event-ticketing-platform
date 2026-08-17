import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.kafka.schemas import EventSeat, EventUpsertedMessage, KafkaAction


def _message_kwargs(**overrides) -> dict:
    start = datetime.now(timezone.utc) + timedelta(days=1)
    kwargs = dict(
        action=KafkaAction.UPSERTED,
        event_id=uuid.uuid4(),
        title="Test Event",
        description=None,
        start_time=start,
        end_time=start + timedelta(hours=2),
        venue_name="Test Arena",
        performer_names=[],
        seats=[EventSeat(section="A", row="1", label="A1", price_cents=2500)],
    )
    kwargs.update(overrides)
    return kwargs


def test_blank_seat_section_is_rejected():
    with pytest.raises(ValidationError):
        EventSeat(section="   ", row="1", label="A1", price_cents=2500)


def test_non_positive_seat_price_is_rejected():
    with pytest.raises(ValidationError):
        EventSeat(section="A", row="1", label="A1", price_cents=0)


def test_blank_event_title_is_rejected():
    with pytest.raises(ValidationError):
        EventUpsertedMessage(**_message_kwargs(title=""))


def test_blank_venue_name_is_rejected():
    with pytest.raises(ValidationError):
        EventUpsertedMessage(**_message_kwargs(venue_name="   "))


def test_valid_message_is_accepted():
    message = EventUpsertedMessage(**_message_kwargs())
    assert message.title == "Test Event"
