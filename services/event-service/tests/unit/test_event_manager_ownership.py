import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared_auth import Principal

from app.api.schemas import EventUpdate, Seat, SeatMapRow, SeatMapSection, SeatMapUpsert
from app.db.models import Event, EventStatus, Venue
from app.logic.event_manager import EventManager

OWNER_SUBJECT = "organizer-owner"
OTHER_ORGANIZER_SUBJECT = "organizer-other"


def make_event(organizer_id: str = OWNER_SUBJECT) -> Event:
    now = datetime.now(timezone.utc)
    venue = Venue(id=uuid.uuid4(), name="Test Venue", address="1 Test St", capacity=100)
    event = Event(
        id=uuid.uuid4(),
        title="Owned Event",
        description=None,
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=1, hours=2),
        status=EventStatus.DRAFT,
        organizer_id=organizer_id,
        venue_id=venue.id,
    )
    event.venue = venue
    event.performers = []
    return event


def make_manager(event: Event) -> EventManager:
    manager = EventManager(session=MagicMock(), mongo_db=MagicMock(), producer=AsyncMock())
    manager._session.commit = AsyncMock()
    manager._events = MagicMock()
    manager._events.get_by_id = AsyncMock(return_value=event)
    manager._events.delete = AsyncMock(return_value=True)
    manager._seat_maps = MagicMock()
    manager._seat_maps.delete = AsyncMock()
    manager._seat_maps.get_by_event_id = AsyncMock(return_value=None)
    manager._seat_maps.upsert = AsyncMock()
    return manager


SEAT_MAP_PAYLOAD = SeatMapUpsert(
    sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
)


async def test_owning_organizer_can_update():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    result = await manager.update_event(user, event.id, EventUpdate(title="Updated Title"))

    assert result.title == "Updated Title"


async def test_non_owning_organizer_cannot_update():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.update_event(user, event.id, EventUpdate(title="Hijacked"))

    assert exc_info.value.status_code == 403


async def test_owning_organizer_can_delete():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    await manager.delete_event(user, event.id)

    manager._events.delete.assert_awaited_once_with(event)
    manager._seat_maps.delete.assert_awaited_once_with(event.id)


async def test_non_owning_organizer_cannot_delete():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(user, event.id)

    assert exc_info.value.status_code == 403
    manager._events.delete.assert_not_awaited()


async def test_owning_organizer_can_upsert_seat_map():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    result = await manager.upsert_seat_map(user, event.id, SEAT_MAP_PAYLOAD)

    assert result.sections[0].name == "A"
    manager._seat_maps.upsert.assert_awaited_once()
    manager._producer.publish_upserted.assert_not_awaited()  # DRAFT event: no republish


async def test_non_owning_organizer_cannot_upsert_seat_map():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.upsert_seat_map(user, event.id, SEAT_MAP_PAYLOAD)

    assert exc_info.value.status_code == 403
    manager._seat_maps.upsert.assert_not_awaited()


async def test_seat_map_upsert_republishes_a_published_event():
    event = make_event()
    event.status = EventStatus.PUBLISHED
    manager = make_manager(event)
    # No manager._seat_maps.get_by_event_id override needed: upsert_seat_map
    # hands _republish the seat map it just upserted directly, so the
    # republish path never re-fetches from Mongo.
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    await manager.upsert_seat_map(user, event.id, SEAT_MAP_PAYLOAD)

    manager._producer.publish_upserted.assert_awaited_once()
    published_event, published_seat_map = manager._producer.publish_upserted.await_args.args
    assert published_event is event
    assert published_seat_map.sections[0].name == "A"


async def test_delete_reports_not_found_when_a_concurrent_delete_won_the_race():
    # Repository.delete() returns False when its DELETE matched zero rows -- the
    # row was already gone by the time this request's statement ran. The manager
    # must treat that as a 404, not a second success with a second round of
    # side effects (duplicate Kafka `deleted` message, redundant Mongo delete).
    event = make_event()
    manager = make_manager(event)
    manager._events.delete = AsyncMock(return_value=False)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(user, event.id)

    assert exc_info.value.status_code == 404
    manager._session.commit.assert_not_awaited()
    manager._seat_maps.delete.assert_not_awaited()


async def test_update_rejects_end_time_before_existing_start_time():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.update_event(user, event.id, EventUpdate(end_time=event.start_time - timedelta(hours=1)))

    assert exc_info.value.status_code == 422
