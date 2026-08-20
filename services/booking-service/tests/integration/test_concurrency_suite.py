import asyncio
import uuid

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from shared_auth import Principal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.booking_repository import BookingRepository
from app.db.event_repository import EventRepository
from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository
from app.logic.booking_manager import BookingManager
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy
from app.logic.helpers.redis_hold_strategy import RedisHoldStrategy

from .conftest import seed_ticket

pytestmark = pytest.mark.asyncio

N_CLIENTS = 25


async def _count_bookings_for_ticket(session_factory: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(Booking).where(Booking.ticket_id == ticket_id))
        return result.scalar_one()


async def _attempt_booking(ticket_id: uuid.UUID, user_subject: str, session_factory, make_hold_strategy) -> bool:
    async with session_factory() as session:
        manager = BookingManager(
            session=session,
            tickets=TicketRepository(session),
            bookings=BookingRepository(session),
            hold_strategy=make_hold_strategy(session),
            events=EventRepository(session),
        )
        try:
            await manager.create_booking(Principal(subject=user_subject, roles=[]), ticket_id)
            return True
        except HTTPException as exc:
            # A losing client should see a clean 409 (either hold-strategy
            # rejection or the uq_bookings_active_ticket defense-in-depth
            # catch) — anything else, including a non-HTTPException crash,
            # is a real bug and must fail the test rather than be counted as
            # an ordinary loss. A too-broad `except Exception` here
            # previously masked exactly that: a losing client crashing with
            # sqlalchemy.exc.MissingGreenlet inside the IntegrityError
            # compensation path (booking_manager.py's release_hold call
            # accessing an expired ORM attribute post-rollback) looked
            # identical to a clean loss under `except Exception: return
            # False`, so this exact bug passed this test undetected.
            assert exc.status_code == 409, f"expected a clean 409 loss, got {exc.status_code}: {exc.detail}"
            return False


async def test_n_clients_race_one_seat_under_cron_strategy_exactly_one_wins(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    ticket_id = await seed_ticket(db_session_factory)

    results = await asyncio.gather(
        *(
            _attempt_booking(ticket_id, f"user-{i}", db_session_factory, CronHoldStrategy)
            for i in range(N_CLIENTS)
        )
    )

    assert sum(results) == 1
    assert await _count_bookings_for_ticket(db_session_factory, ticket_id) == 1


async def test_n_clients_race_one_seat_under_redis_strategy_exactly_one_wins(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: Redis
):
    ticket_id = await seed_ticket(db_session_factory)

    results = await asyncio.gather(
        *(
            _attempt_booking(ticket_id, f"user-{i}", db_session_factory, lambda _session: RedisHoldStrategy(redis_client))
            for i in range(N_CLIENTS)
        )
    )

    assert sum(results) == 1
    assert await _count_bookings_for_ticket(db_session_factory, ticket_id) == 1


async def test_duplicate_provisioning_message_creates_no_duplicate_tickets(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    event_id = uuid.uuid4()
    seats = [("A", "1", "A1", 2500), ("A", "1", "A2", 2500)]
    async with db_session_factory() as session:
        repo = TicketRepository(session)
        first = await repo.bulk_upsert_available(event_id, seats)
        await session.commit()
        second = await repo.bulk_upsert_available(event_id, seats)  # redelivery
        await session.commit()

    assert first == 2
    assert second == 0


async def test_abandoned_hold_releases_under_cron_strategy_within_bounded_wait(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        acquired = await CronHoldStrategy(session).acquire_hold(ticket_id, ttl_seconds=0)
        await session.commit()
    assert acquired is True

    async with db_session_factory() as session:
        released = await CronHoldStrategy(session).release_expired()
        await session.commit()
    assert released == 1

    async with db_session_factory() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket.status is TicketStatus.AVAILABLE


# No redis-strategy counterpart here: under the redis strategy, abandoned-hold
# release is plain Redis TTL expiry with no sweep involved, already covered by
# test_redis_hold_race.py::test_abandoned_hold_auto_releases_on_ttl_no_sweep_needed.


async def test_integrity_race_compensation_does_not_crash(db_session_factory: async_sessionmaker[AsyncSession]):
    # Reproduces, directly rather than probabilistically, the exact state a
    # live session found: a ticket whose status says AVAILABLE while an
    # active (PENDING) Booking row still references it — the hold strategy
    # has no way to see this and correctly grants the hold, so
    # _create_booking_row reaches the uq_bookings_active_ticket unique index
    # and must hit the IntegrityError compensation path. That path
    # previously crashed with sqlalchemy.exc.MissingGreenlet (accessing
    # ticket.id on an ORM instance whose attributes session.rollback() had
    # just expired) instead of returning a clean 409 — found live via two
    # real concurrent curl requests, reproduced here deterministically.
    ticket_id = await seed_ticket(db_session_factory)
    async with db_session_factory() as session:
        session.add(
            Booking(
                user_subject="stale-user", event_id=uuid.uuid4(), ticket_id=ticket_id, status=BookingStatus.PENDING
            )
        )
        await session.commit()

    async with db_session_factory() as session:
        manager = BookingManager(
            session=session,
            tickets=TicketRepository(session),
            bookings=BookingRepository(session),
            hold_strategy=CronHoldStrategy(session),
            events=EventRepository(session),
        )
        with pytest.raises(HTTPException) as exc_info:
            await manager.create_booking(Principal(subject="racing-user", roles=[]), ticket_id)
        assert exc_info.value.status_code == 409

    assert await _count_bookings_for_ticket(db_session_factory, ticket_id) == 1
