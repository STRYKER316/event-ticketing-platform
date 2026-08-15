from datetime import datetime, timedelta, timezone

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
    manager = EventManager(db_session, mongo_db)
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
            event_id=str(created.id),
            sections=[SeatMapSection(name="A", rows=[SeatMapRow(name="1", seats=[Seat(label="A1", x=0, y=0)])])],
        )
    )
    seat_map = await manager.get_seat_map(created.id)
    assert seat_map.event_id == str(created.id)
    assert seat_map.sections[0].name == "A"


async def test_cross_organizer_update_is_rejected(db_session: AsyncSession, mongo_db: AsyncIOMotorDatabase):
    venue = await _seed_venue(db_session)
    manager = EventManager(db_session, mongo_db)
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
