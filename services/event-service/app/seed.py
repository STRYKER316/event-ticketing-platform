import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog

from app.api.schemas import Seat, SeatMap, SeatMapRow, SeatMapSection
from app.core import configure_logging, get_mongo_db, get_session_factory
from app.db.event_repository import EventRepository
from app.db.models import Event, EventStatus, Performer, Venue
from app.db.seat_map_repository import SeatMapRepository

logger = structlog.get_logger()

SEED_ORGANIZER_ID = "seed-organizer"


def _rectangular_seat_map(event_id: uuid.UUID, rows: int, seats_per_row: int) -> SeatMap:
    sections = [
        SeatMapSection(
            name="General",
            price_cents=2500,
            rows=[
                SeatMapRow(
                    name=str(row),
                    seats=[Seat(label=f"{row}-{seat}", x=float(seat), y=float(row)) for seat in range(1, seats_per_row + 1)],
                )
                for row in range(1, rows + 1)
            ],
        )
    ]
    return SeatMap(event_id=event_id, sections=sections)


async def seed(session_factory, mongo_db) -> None:
    async with session_factory() as session:
        events = await EventRepository(session).list(limit=1, offset=0, sort_field="start_time", sort_desc=False)
        if events:
            logger.info("seed_skipped_data_already_present")
            return

        venues = [
            Venue(name="Riverside Arena", address="1 River Rd, Springfield", capacity=8000),
            Venue(name="Downtown Theater", address="42 Main St, Springfield", capacity=1200),
        ]
        performers = [
            Performer(name="The Wandering Notes", bio="Indie rock quartet"),
            Performer(name="Springfield Philharmonic", bio="City orchestra"),
            Performer(name="Comedy Night Live", bio="Stand-up showcase"),
        ]
        session.add_all(venues + performers)
        await session.flush()

        now = datetime.now(timezone.utc)
        events = [
            Event(
                title="Wandering Notes: Reunion Tour",
                description="First show in five years.",
                start_time=now - timedelta(days=10),
                end_time=now - timedelta(days=10) + timedelta(hours=3),
                status=EventStatus.PUBLISHED,
                organizer_id=SEED_ORGANIZER_ID,
                venue_id=venues[0].id,
            ),
            Event(
                title="Philharmonic: Spring Concert",
                description="An evening of classical favorites.",
                start_time=now + timedelta(days=14),
                end_time=now + timedelta(days=14) + timedelta(hours=2),
                status=EventStatus.PUBLISHED,
                organizer_id=SEED_ORGANIZER_ID,
                venue_id=venues[1].id,
            ),
            Event(
                title="Comedy Night Live: Spring Showcase",
                description="Five comedians, one stage.",
                start_time=now + timedelta(days=30),
                end_time=now + timedelta(days=30) + timedelta(hours=2),
                status=EventStatus.PUBLISHED,
                organizer_id=SEED_ORGANIZER_ID,
                venue_id=venues[1].id,
            ),
        ]
        events[0].performers = [performers[0]]
        events[1].performers = [performers[1]]
        events[2].performers = [performers[2]]
        session.add_all(events)
        await session.commit()

        seat_map_repo = SeatMapRepository(mongo_db)
        seat_map = _rectangular_seat_map(events[0].id, rows=10, seats_per_row=20)
        await seat_map_repo.upsert(seat_map)

        logger.info(
            "seed_completed",
            venues=len(venues),
            performers=len(performers),
            events=len(events),
            seat_maps=1,
        )


async def main() -> None:
    configure_logging()
    await seed(get_session_factory(), get_mongo_db())


if __name__ == "__main__":
    asyncio.run(main())
