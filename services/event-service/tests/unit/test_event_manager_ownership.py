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
    sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
)


async def test_owning_organizer_can_update():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    result = await manager.update_event(user, event.id, EventUpdate(title="Updated Title"))

    assert result.title == "Updated Title"


async def test_non_owning_organizer_cannot_update():
    # DRAFT event: hidden from non-owners (404), not merely forbidden (403).
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.update_event(user, event.id, EventUpdate(title="Hijacked"))

    assert exc_info.value.status_code == 404


async def test_non_owning_organizer_gets_403_updating_a_published_event():
    # PUBLISHED event: existence is already public, so mutation is merely
    # forbidden (403), not hidden (404) — unlike the DRAFT case above.
    event = make_event()
    event.status = EventStatus.PUBLISHED
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

    manager._events.delete.assert_awaited_once_with(event.id)
    manager._seat_maps.delete.assert_awaited_once_with(event.id)


async def test_non_owning_organizer_cannot_delete():
    # DRAFT event: hidden from non-owners (404), not merely forbidden (403).
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(user, event.id)

    assert exc_info.value.status_code == 404
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
    # DRAFT event: hidden from non-owners (404), not merely forbidden (403).
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.upsert_seat_map(user, event.id, SEAT_MAP_PAYLOAD)

    assert exc_info.value.status_code == 404
    manager._seat_maps.upsert.assert_not_awaited()


async def test_seat_map_upsert_rejected_once_event_is_published():
    # Booking Service may already have provisioned Ticket rows from the current
    # seat map once PUBLISHED (§7.2) — mutating it in place is refused the same
    # way delete is, rather than silently republished.
    event = make_event()
    event.status = EventStatus.PUBLISHED
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.upsert_seat_map(user, event.id, SEAT_MAP_PAYLOAD)

    assert exc_info.value.status_code == 409
    manager._seat_maps.upsert.assert_not_awaited()
    manager._producer.publish_upserted.assert_not_awaited()


async def test_delete_reports_not_found_when_a_concurrent_delete_won_the_race():
    # delete() returning False (row already gone) must be a 404, not a second success.
    event = make_event()
    manager = make_manager(event)
    manager._events.delete = AsyncMock(return_value=False)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(user, event.id)

    assert exc_info.value.status_code == 404
    manager._session.commit.assert_not_awaited()
    manager._seat_maps.delete.assert_not_awaited()


async def test_owning_organizer_cannot_delete_a_published_event():
    # Event Service can't see booking_db (§8) to check for live bookings, so
    # a PUBLISHED event is refused outright rather than conditionally
    # checked (Task 6 amendment, decisions-log delta).
    event = make_event()
    event.status = EventStatus.PUBLISHED
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(user, event.id)

    assert exc_info.value.status_code == 409
    manager._events.delete.assert_not_awaited()


async def test_update_rejects_end_time_before_existing_start_time():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.update_event(user, event.id, EventUpdate(end_time=event.start_time - timedelta(hours=1)))

    assert exc_info.value.status_code == 422


# --- DRAFT visibility scoping (§15) ---
# A DRAFT event's existence and full seat map must not be readable by
# anyone but its owning organizer, even by guessing the event ID.


async def test_draft_event_is_hidden_from_non_owning_organizer():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.get_event(event.id, user)

    assert exc_info.value.status_code == 404


async def test_draft_event_is_hidden_from_an_anonymous_caller():
    event = make_event()
    manager = make_manager(event)

    with pytest.raises(HTTPException) as exc_info:
        await manager.get_event(event.id, None)

    assert exc_info.value.status_code == 404


async def test_draft_event_is_visible_to_its_owning_organizer():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    result = await manager.get_event(event.id, user)

    assert result.id == event.id


async def test_draft_seat_map_is_hidden_from_non_owning_organizer_without_ever_querying_mongo():
    event = make_event()
    manager = make_manager(event)
    user = Principal(subject=OTHER_ORGANIZER_SUBJECT, roles=["organizer"])

    with pytest.raises(HTTPException) as exc_info:
        await manager.get_seat_map(event.id, user)

    assert exc_info.value.status_code == 404
    manager._seat_maps.get_by_event_id.assert_not_awaited()


async def test_draft_seat_map_is_hidden_from_an_anonymous_caller():
    event = make_event()
    manager = make_manager(event)

    with pytest.raises(HTTPException) as exc_info:
        await manager.get_seat_map(event.id, None)

    assert exc_info.value.status_code == 404
    manager._seat_maps.get_by_event_id.assert_not_awaited()


async def test_update_omitting_description_leaves_it_unchanged():
    event = make_event()
    event.description = "Original description"
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    result = await manager.update_event(user, event.id, EventUpdate(title="Retitled"))

    assert result.description == "Original description"


async def test_update_with_explicit_null_description_clears_it():
    event = make_event()
    event.description = "Original description"
    manager = make_manager(event)
    user = Principal(subject=OWNER_SUBJECT, roles=["organizer"])

    result = await manager.update_event(user, event.id, EventUpdate(description=None))

    assert result.description is None


async def test_published_event_is_visible_to_anyone():
    event = make_event()
    event.status = EventStatus.PUBLISHED
    manager = make_manager(event)

    result = await manager.get_event(event.id, None)

    assert result.id == event.id
