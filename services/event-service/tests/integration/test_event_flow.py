from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from shared_auth import Principal
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import EventCreate, EventUpdate, Seat, SeatMap, SeatMapRow, SeatMapSection
from app.db.models import Venue
from app.db.seat_map_repository import SeatMapRepository
from app.logic.event_manager import EventManager

pytestmark = pytest.mark.asyncio

ORGANIZER = Principal(subject="organizer-1", roles=["organizer"])
OTHER_ORGANIZER = Principal(subject="organizer-2", roles=["organizer"])


async def _seed_venue(session: AsyncSession) -> Venue:
    venue = Venue(name="Integration Arena", address="1 Test Way", capacity=2000)
    session.add(venue)
    await session.commit()
    return venue


async def test_create_fetch_event_and_seat_map(db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db, producer=AsyncMock())
    start = datetime.now(timezone.utc) + timedelta(days=1)

    created = await manager.create_event(
        ORGANIZER,
        EventCreate(title="Integration Concert", start_time=start, end_time=start + timedelta(hours=2), venue_id=venue.id),
    )

    fetched = await manager.get_event(created.id)
    assert fetched.title == "Integration Concert"
    assert fetched.organizer_id == ORGANIZER.subject
    assert fetched.venue.id == venue.id

    await SeatMapRepository(mongo_db).upsert(
        SeatMap(
            event_id=created.id,
            sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
        )
    )
    seat_map = await manager.get_seat_map(created.id)
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
        sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
    )
    await SeatMapRepository(mongo_db).upsert(seat_map)

    published = await manager.publish_event(ORGANIZER, created.id)
    assert published.status.value == "published"
    producer.publish_upserted.assert_awaited_once()

    await manager.update_event(ORGANIZER, created.id, EventUpdate(title="Renamed Concert"))
    assert producer.publish_upserted.await_count == 2

    await manager.delete_event(ORGANIZER, created.id)
    producer.publish_deleted.assert_awaited_once_with(created.id)


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
            sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
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
    producer.publish_deleted.assert_not_awaited()


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
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        await manager.delete_event(OTHER_ORGANIZER, created.id)
    assert exc_info.value.status_code == 403

    still_there = await manager.get_event(created.id)
    assert still_there.title == "Owned Concert"
