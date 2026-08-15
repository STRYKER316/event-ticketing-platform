import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared_auth import Principal

from app.api.schemas import EventUpdate
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
    manager = EventManager(session=MagicMock(), mongo_db=MagicMock())
    manager._session.commit = AsyncMock()
    manager._events = MagicMock()
    manager._events.get_by_id = AsyncMock(return_value=event)
    manager._events.delete = AsyncMock()
    return manager


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


async def test_non_owning_organizer_cannot_delete():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(user, event.id)

    assert exc_info.value.status_code == 403
    manager._events.delete.assert_not_awaited()


async def test_update_rejects_end_time_before_existing_start_time():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.update_event(user, event.id, EventUpdate(end_time=event.start_time - timedelta(hours=1)))

    assert exc_info.value.status_code == 422
