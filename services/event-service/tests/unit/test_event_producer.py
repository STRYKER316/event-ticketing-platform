import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.api.schemas import Seat, SeatMap, SeatMapRow, SeatMapSection
from app.db.models import Event, EventStatus, Performer, Venue
from app.kafka.producers import EventProducer

TOPIC = "event.events"


def make_event() -> Event:
    now = datetime.now(timezone.utc)
    venue = Venue(id=uuid.uuid4(), name="Test Venue", address="1 Test St", capacity=100)
    event = Event(
        id=uuid.uuid4(),
        title="Test Event",
        description="A description",
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, hours=2),
        status=EventStatus.PUBLISHED,
        organizer_id="organizer-1",
        venue_id=venue.id,
    )
    event.venue = venue
    event.performers = [Performer(id=uuid.uuid4(), name="Performer One", bio=None)]
    return event


SEAT_MAP = SeatMap(
    event_id=uuid.uuid4(),
    sections=[
        SeatMapSection(
            name="A",
            price_cents=2500,
            rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0), Seat(label="A2", x=1, y=0)])],
        )
    ],
)


async def test_publish_upserted_sends_correct_topic_key_and_payload():
    producer = AsyncMock()
    event = make_event()
    event_producer = EventProducer(producer, TOPIC)

    await event_producer.publish_upserted(event, SEAT_MAP)

    producer.send_and_wait.assert_awaited_once()
    call = producer.send_and_wait.await_args
    assert call.args[0] == TOPIC
    assert call.kwargs["key"] == str(event.id).encode()

    payload = json.loads(call.kwargs["value"])
    assert payload["action"] == "upserted"
    assert payload["event_id"] == str(event.id)
    assert payload["title"] == "Test Event"
    assert payload["venue_name"] == "Test Venue"
    assert payload["performer_names"] == ["Performer One"]
    assert payload["seats"] == [
        {"section": "A", "row": "1", "label": "A1", "price_cents": 2500},
        {"section": "A", "row": "1", "label": "A2", "price_cents": 2500},
    ]
