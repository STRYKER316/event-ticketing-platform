from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from shared_auth import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.schemas import (
    EventCreate,
    EventSortField,
    EventUpdate,
    Seat,
    SeatMap,
    SeatMapRow,
    SeatMapSection,
    SeatMapUpsert,
    SortOrder,
    VenueCreate,
)
from app.db.models import Venue
from app.db.seat_map_repository import SeatMapRepository
from app.logic.event_manager import EventManager
from app.logic.venue_manager import VenueManager

pytestmark = pytest.mark.asyncio

ORGANIZER = Principal(subject="organizer-1", roles=["organizer"])
OTHER_ORGANIZER = Principal(subject="organizer-2", roles=["organizer"])


async def _seed_venue(session: AsyncSession) -> Venue:
    venue = Venue(name="Integration Arena", address="1 Test Way", capacity=2000)
    session.add(venue)
    await session.commit()
    return venue


async def test_create_venue_commits_visibly_to_a_second_connection(db_session: AsyncSession, _migrated_database_url: str):
    manager = VenueManager(db_session)

    created = await manager.create_venue(VenueCreate(name="New Arena", address="9 New St", capacity=500))

    # A query against db_session itself would pass even without a commit (flush() alone is visible there); only a separate connection under READ COMMITTED can tell the two apart.
    other_engine = create_async_engine(_migrated_database_url)
    async with other_engine.connect() as conn:
        row = (await conn.execute(text("SELECT name, capacity FROM venues WHERE id = :id"), {"id": str(created.id)})).one()
    await other_engine.dispose()

    assert row.name == "New Arena"
    assert row.capacity == 500


async def test_owning_organizer_can_upsert_seat_map_via_api_path(
    db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase
):
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Needs Seats", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    payload = SeatMapUpsert(
        sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
    )
    result = await manager.upsert_seat_map(ORGANIZER, created.id, payload)
    assert result.sections[0].name == "A"

    fetched = await manager.get_seat_map(created.id, ORGANIZER)
    assert fetched.sections[0].rows[0].seats[0].label == "A1"


async def test_cross_organizer_cannot_upsert_seat_map(db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Owned Seats", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    payload = SeatMapUpsert(
        sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
    )
    with pytest.raises(HTTPException) as exc_info:
        await manager.upsert_seat_map(OTHER_ORGANIZER, created.id, payload)
    assert exc_info.value.status_code == 404  # DRAFT event: hidden from non-owners


async def test_seat_map_upsert_is_rejected_once_event_is_published(
    db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase
):
    # Booking Service may already have provisioned Tickets from the current seat map once PUBLISHED, so mutation is refused, same as delete.
    venue = await _seed_venue(db_session)
    producer = AsyncMock()
    manager = EventManager(db_session, mongo_db, producer=producer)
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Immutable After Publish", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )
    initial_payload = SeatMapUpsert(
        sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]
    )
    await manager.upsert_seat_map(ORGANIZER, created.id, initial_payload)
    await manager.publish_event(ORGANIZER, created.id)
    producer.publish_upserted.assert_awaited_once()

    updated_payload = SeatMapUpsert(
        sections=[SeatMapSection(price_cents=2500, name="B", rows=[SeatMapRow(name="1", seats=[Seat(label="B1", x=0, y=0)])])]
    )
    with pytest.raises(HTTPException) as exc_info:
        await manager.upsert_seat_map(ORGANIZER, created.id, updated_payload)
    assert exc_info.value.status_code == 409

    # Rejected before publishing again, and the original seat map is untouched.
    producer.publish_upserted.assert_awaited_once()
    unchanged = await manager.get_seat_map(created.id, ORGANIZER)
    assert unchanged.sections[0].name == "A"


async def test_create_fetch_event_and_seat_map(db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Integration Concert", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    fetched = await manager.get_event(created.id, ORGANIZER)
    assert fetched.title == "Integration Concert"
    assert fetched.organizer_id == ORGANIZER.subject
    assert fetched.venue.id == venue.id

    await SeatMapRepository(mongo_db).upsert(
        SeatMap(
            event_id=created.id,
            sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
        )
    )
    seat_map = await manager.get_seat_map(created.id, ORGANIZER)
    assert seat_map.event_id == created.id
    assert seat_map.sections[0].name == "A"

    await manager.delete_event(ORGANIZER, created.id)
    assert await SeatMapRepository(mongo_db).get_by_event_id(created.id) is None


async def test_publish_notifies_producer_and_republishes_on_update(
    db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase
):
    venue = await _seed_venue(db_session)
    producer = AsyncMock()
    manager = EventManager(db_session, mongo_db, producer=producer)
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Publishable Concert", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )
    producer.publish_upserted.assert_not_awaited()

    with pytest.raises(HTTPException) as exc_info:
        await manager.publish_event(ORGANIZER, created.id)
    assert exc_info.value.status_code == 422

    seat_map = SeatMap(
        event_id=created.id,
        sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
    )
    await SeatMapRepository(mongo_db).upsert(seat_map)

    published = await manager.publish_event(ORGANIZER, created.id)
    assert published.status.value == "published"
    producer.publish_upserted.assert_awaited_once()

    await manager.update_event(ORGANIZER, created.id, EventUpdate(title="Renamed Concert"))
    assert producer.publish_upserted.await_count == 2

    # A published event can never be deleted: Event Service has no channel to check Booking Service for live bookings, so deletion is refused outright.
    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(ORGANIZER, created.id)
    assert exc_info.value.status_code == 409
    assert producer.publish_upserted.await_count == 2


async def test_republish_on_venue_change_reflects_the_new_venue(
    db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase
):
    original_venue = await _seed_venue(db_session)
    new_venue = Venue(name="New Venue", address="2 Test Way", capacity=500)
    db_session.add(new_venue)
    await db_session.commit()

    producer = AsyncMock()
    manager = EventManager(db_session, mongo_db, producer=producer)
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(
            title="Venue Change Concert", start_time=start, end_time=start + timedelta(hours=2), venue_id=original_venue.id
        ),
    )
    await SeatMapRepository(mongo_db).upsert(
        SeatMap(
            event_id=created.id,
            sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
        )
    )
    await manager.publish_event(ORGANIZER, created.id)

    await manager.update_event(ORGANIZER, created.id, EventUpdate(venue_id=new_venue.id))

    republished_event = producer.publish_upserted.await_args.args[0]
    assert republished_event.venue.name == "New Venue"


async def test_deleting_a_draft_event_does_not_notify_producer(db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
    venue = await _seed_venue(db_session)
    producer = AsyncMock()
    manager = EventManager(db_session, mongo_db, producer=producer)
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Never Published", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    await manager.delete_event(ORGANIZER, created.id)
    producer.publish_upserted.assert_not_awaited()


async def test_repository_delete_reports_false_when_the_row_is_already_gone(
    db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase
):
    # Proves the rowcount-based race fix: deleting the same event ID twice, real rowcount is 0 the second time.
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Deleted Twice", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    first = await manager._events.delete(created.id)
    second = await manager._events.delete(created.id)

    assert first is True
    assert second is False


async def test_cross_organizer_update_is_rejected(db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Owned Concert", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.update_event(OTHER_ORGANIZER, created.id, EventUpdate(title="Hijacked"))
    assert exc_info.value.status_code == 404  # DRAFT event: hidden from non-owners

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(OTHER_ORGANIZER, created.id)
    assert exc_info.value.status_code == 404  # DRAFT event: hidden from non-owners

    still_there = await manager.get_event(created.id, ORGANIZER)
    assert still_there.title == "Owned Concert"


async def test_draft_event_and_seat_map_hidden_from_non_owner_and_anonymous(
    db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase
):
    # A DRAFT event's existence and full seat map (pricing, layout) must not be readable by anyone who guesses/enumerates the event ID.
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Private Draft", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )
    await SeatMapRepository(mongo_db).upsert(
        SeatMap(
            event_id=created.id,
            sections=[SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
        )
    )

    for caller in (OTHER_ORGANIZER, None):
        with pytest.raises(HTTPException) as exc_info:
            await manager.get_event(created.id, caller)
        assert exc_info.value.status_code == 404

        with pytest.raises(HTTPException) as exc_info:
            await manager.get_seat_map(created.id, caller)
        assert exc_info.value.status_code == 404

    # The owning organizer can still see both.
    visible_event = await manager.get_event(created.id, ORGANIZER)
    assert visible_event.title == "Private Draft"
    visible_seat_map = await manager.get_seat_map(created.id, ORGANIZER)
    assert visible_seat_map.sections[0].name == "A"


async def test_public_listing_excludes_draft_events(db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
    # Regression test: EventRepository.list built no WHERE clause on status, so DRAFT events leaked into the public GET /events listing.
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)
    seat_map_sections = [SeatMapSection(price_cents=2500, name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])]

    draft = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Still Draft", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    published = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Published Event", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )
    await SeatMapRepository(mongo_db).upsert(SeatMap(event_id=published.id, sections=seat_map_sections))
    await manager.publish_event(ORGANIZER, published.id)

    listing = await manager.list_events(
        limit=20, offset=0, sort_field=EventSortField.START_TIME, sort_order=SortOrder.ASC
    )

    titles = {item.title for item in listing.items}
    assert "Published Event" in titles
    assert "Still Draft" not in titles
    assert listing.total == 1
    assert draft.status.value == "draft"
