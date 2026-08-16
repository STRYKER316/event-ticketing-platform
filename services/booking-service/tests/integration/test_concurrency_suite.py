import asyncio
import uuid

import pytest
from redis.asyncio import Redis
from shared_auth import Principal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.booking_repository import BookingRepository
from app.db.models import Booking, Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository
from app.logic.booking_manager import BookingManager
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy
from app.logic.helpers.redis_hold_strategy import RedisHoldStrategy

pytestmark = pytest.mark.asyncio

N_CLIENTS = 25


async def _seed_ticket(session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with session_factory() as session:
        ticket = Ticket(event_id=uuid.uuid4(), section="A", row_name="1", seat_label="A1", status=TicketStatus.AVAILABLE)
        session.add(ticket)
        await session.commit()
        return ticket.id


async def _count_bookings_for_ticket(session_factory: async_sessionmaker[AsyncSession], ticket_id: uuid.UUID) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(Booking).where(Booking.ticket_id == ticket_id))
        return result.scalar_one()


async def _attempt_booking_cron(ticket_id: uuid.UUID, user_subject: str, session_factory) -> bool:
    async with session_factory() as session:
        manager = BookingManager(
            session=session,
            tickets=TicketRepository(session),
            bookings=BookingRepository(session),
            hold_strategy=CronHoldStrategy(session),
        )
        try:
            await manager.create_booking(Principal(subject=user_subject, roles=[]), ticket_id)
            return True
        except Exception:
            return False


async def test_n_clients_race_one_seat_under_cron_strategy_exactly_one_wins(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    ticket_id = await _seed_ticket(db_session_factory)

    results = await asyncio.gather(
        *(_attempt_booking_cron(ticket_id, f"user-{i}", db_session_factory) for i in range(N_CLIENTS))
    )

    assert sum(results) == 1
    assert await _count_bookings_for_ticket(db_session_factory, ticket_id) == 1


async def _attempt_booking_redis(ticket_id: uuid.UUID, user_subject: str, session_factory, redis: Redis) -> bool:
    async with session_factory() as session:
        manager = BookingManager(
            session=session,
            tickets=TicketRepository(session),
            bookings=BookingRepository(session),
            hold_strategy=RedisHoldStrategy(redis),
        )
        try:
            await manager.create_booking(Principal(subject=user_subject, roles=[]), ticket_id)
            return True
        except Exception:
            return False


async def test_n_clients_race_one_seat_under_redis_strategy_exactly_one_wins(
    db_session_factory: async_sessionmaker[AsyncSession], redis_container
):
    ticket_id = await _seed_ticket(db_session_factory)
    redis = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(redis_container.port)),
        decode_responses=True,
    )

    results = await asyncio.gather(
        *(_attempt_booking_redis(ticket_id, f"user-{i}", db_session_factory, redis) for i in range(N_CLIENTS))
    )

    assert sum(results) == 1
    assert await _count_bookings_for_ticket(db_session_factory, ticket_id) == 1

    await redis.flushall()
    await redis.aclose()


async def test_duplicate_provisioning_message_creates_no_duplicate_tickets(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    event_id = uuid.uuid4()
    seats = [("A", "1", "A1"), ("A", "1", "A2")]
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
    ticket_id = await _seed_ticket(db_session_factory)
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


async def test_abandoned_hold_releases_under_redis_strategy_within_bounded_wait(redis_container):
    ticket_id = uuid.uuid4()
    redis = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(redis_container.port)),
        decode_responses=True,
    )
    strategy = RedisHoldStrategy(redis)

    assert await strategy.acquire_hold(ticket_id, ttl_seconds=1) is True
    await asyncio.sleep(1.5)
    assert await strategy.is_held(ticket_id) is False
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True

    await redis.flushall()
    await redis.aclose()
