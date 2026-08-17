import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared_auth import Principal

from app.api.schemas import Seat, SeatMap, SeatMapRow, SeatMapSection
from app.db.models import Event, EventStatus, Venue
from app.logic.event_manager import EventManager

OWNER = Principal(subject="organizer-owner", roles=["organizer"])

SEAT_MAP = SeatMap(
    event_id=uuid.uuid4(),
    sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
)


def make_draft_event() -> Event:
    now = datetime.now(timezone.utc)
    venue = Venue(id=uuid.uuid4(), name="Test Venue", address="1 Test St", capacity=100)
    event = Event(
        id=uuid.uuid4(),
        title="Draft Event",
        description=None,
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, hours=2),
        status=EventStatus.DRAFT,
        organizer_id=OWNER.subject,
        venue_id=venue.id,
    )
    event.venue = venue
    event.performers = []
    return event


def make_manager(event: Event, seat_map: SeatMap | None) -> EventManager:
    manager = EventManager(session=MagicMock(), mongo_db=MagicMock(), producer=AsyncMock())
    manager._session.commit = AsyncMock()
    manager._events = MagicMock()
    manager._events.get_by_id = AsyncMock(return_value=event)
    manager._seat_maps = MagicMock()
    manager._seat_maps.get_by_event_id = AsyncMock(return_value=seat_map)
    return manager


async def test_publish_missing_seat_map_is_rejected():
    event = make_draft_event()
    manager = make_manager(event, seat_map=None)

    with pytest.raises(HTTPException) as exc_info:
        await manager.publish_event(OWNER, event.id)

    assert exc_info.value.status_code == 422
    manager._producer.publish_upserted.assert_not_awaited()


async def test_publish_sets_status_and_notifies_producer():
    event = make_draft_event()
    manager = make_manager(event, seat_map=SEAT_MAP)

    result = await manager.publish_event(OWNER, event.id)

    assert result.status is EventStatus.PUBLISHED
    assert event.status is EventStatus.PUBLISHED
    manager._producer.publish_upserted.assert_awaited_once_with(event, SEAT_MAP)


async def test_publish_already_published_is_rejected():
    event = make_draft_event()
    event.status = EventStatus.PUBLISHED
    manager = make_manager(event, seat_map=SEAT_MAP)

    with pytest.raises(HTTPException) as exc_info:
        await manager.publish_event(OWNER, event.id)

    assert exc_info.value.status_code == 409
    manager._producer.publish_upserted.assert_not_awaited()
