import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Ticket, TicketStatus
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy
from app.logic.helpers.redis_hold_strategy import RedisHoldStrategy

from .conftest import seed_ticket

pytestmark = pytest.mark.asyncio


async def test_cron_strategy_satisfies_the_shared_contract(db_session_factory: async_sessionmaker[AsyncSession]):
    # Same contract already proven fake-only in tests/unit/test_hold_strategy_contract.py
    # (P3.T3) — this proves CronHoldStrategy satisfies it too, against real Postgres.
    ticket_id = await seed_ticket(db_session_factory)

    async with db_session_factory() as session:
        strategy = CronHoldStrategy(session)
        assert await strategy.is_held(ticket_id) is False
        assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True
        await session.commit()

    async with db_session_factory() as session:
        strategy = CronHoldStrategy(session)
        assert await strategy.is_held(ticket_id) is True
        assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is False
        await strategy.release_hold(ticket_id)
        await session.commit()

    async with db_session_factory() as session:
        strategy = CronHoldStrategy(session)
        assert await strategy.is_held(ticket_id) is False
        assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True
        await session.commit()

    async with db_session_factory() as session:
        strategy = CronHoldStrategy(session)
        await strategy.confirm_hold(ticket_id)
        await session.commit()

    async with db_session_factory() as session:
        strategy = CronHoldStrategy(session)
        assert await strategy.is_held(ticket_id) is False
        ticket = await session.get(Ticket, ticket_id)
        assert ticket.status is TicketStatus.BOOKED


async def test_redis_strategy_satisfies_the_shared_contract(redis_client: Redis):
    # Same contract, proven against real Redis — CronHoldStrategy and
    # RedisHoldStrategy satisfy the identical TicketHoldStrategy interface
    # despite storing hold state in entirely different places (§6).
    ticket_id = uuid.uuid4()
    strategy = RedisHoldStrategy(redis_client)

    assert await strategy.is_held(ticket_id) is False
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True
    assert await strategy.is_held(ticket_id) is True
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is False
    await strategy.release_hold(ticket_id)
    assert await strategy.is_held(ticket_id) is False

    await strategy.acquire_hold(ticket_id, ttl_seconds=60)
    await strategy.confirm_hold(ticket_id)
    assert await strategy.is_held(ticket_id) is False
