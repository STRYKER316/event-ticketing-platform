import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from shared_auth import Principal

from app.api.schemas import EventCreate, Seat, SeatMapRow, SeatMapSection, SeatMapUpsert
from app.core import close_kafka_producer, configure_logging, get_mongo_db, get_session_factory
from app.db.event_repository import EventRepository
from app.db.models import Performer, Venue
from app.kafka.producers import get_event_producer
from app.logic.event_manager import EventManager

logger = structlog.get_logger()

SEED_ORGANIZER = Principal(subject="seed-organizer", roles=["organizer"])


@dataclass
class _SeedEvent:
    title: str
    description: str
    start_time: datetime
    venue_index: int
    performer_indices: list[int]
    seat_rows: int
    seats_per_row: int
    price_cents: int = 2500


def _seat_map_sections(rows: int, seats_per_row: int, price_cents: int) -> list[SeatMapSection]:
    return [
        SeatMapSection(
            name="General",
            price_cents=price_cents,
            rows=[
                SeatMapRow(
                    name=str(row),
                    seats=[Seat(label=f"{row}-{seat}", x=float(seat), y=float(row)) for seat in range(1, seats_per_row + 1)],
                )
                for row in range(1, rows + 1)
            ],
        )
    ]


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
        # All start times are in the future: publish_event rejects a start_time already in the past, so a seeded "already happened" event wouldn't publish.
        seed_events = [
            _SeedEvent(
                title="Wandering Notes: Reunion Tour",
                description="First show in five years.",
                start_time=now + timedelta(days=7),
                venue_index=0,
                performer_indices=[0],
                seat_rows=10,
                seats_per_row=20,
            ),
            _SeedEvent(
                title="Philharmonic: Spring Concert",
                description="An evening of classical favorites.",
                start_time=now + timedelta(days=14),
                venue_index=1,
                performer_indices=[1],
                seat_rows=8,
                seats_per_row=15,
            ),
            _SeedEvent(
                title="Comedy Night Live: Spring Showcase",
                description="Five comedians, one stage.",
                start_time=now + timedelta(days=30),
                venue_index=1,
                performer_indices=[2],
                seat_rows=6,
                seats_per_row=12,
            ),
        ]

        # Routed through the real EventManager path (not direct DB rows) so a fresh seed actually reaches Search and Booking Service via Kafka, not just rows claiming status="published".
        producer = await get_event_producer()
        manager = EventManager(session, mongo_db, producer)
        for spec in seed_events:
            created = await manager.create_event(
                SEED_ORGANIZER,
                EventCreate(
                    title=spec.title,
                    description=spec.description,
                    start_time=spec.start_time,
                    end_time=spec.start_time + timedelta(hours=2),
                    venue_id=venues[spec.venue_index].id,
                    performer_ids=[performers[i].id for i in spec.performer_indices],
                ),
            )
            await manager.upsert_seat_map(
                SEED_ORGANIZER,
                created.id,
                SeatMapUpsert(sections=_seat_map_sections(spec.seat_rows, spec.seats_per_row, spec.price_cents)),
            )
            await manager.publish_event(SEED_ORGANIZER, created.id)

        logger.info(
            "seed_completed",
            venues=len(venues),
            performers=len(performers),
            events=len(seed_events),
            seat_maps=len(seed_events),
        )


async def main() -> None:
    configure_logging()
    try:
        await seed(get_session_factory(), get_mongo_db())
    finally:
        await close_kafka_producer()


if __name__ == "__main__":
    asyncio.run(main())
