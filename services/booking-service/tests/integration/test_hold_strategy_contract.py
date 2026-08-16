import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Ticket, TicketStatus
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy
from app.logic.helpers.redis_hold_strategy import RedisHoldStrategy

pytestmark = pytest.mark.asyncio


async def _seed_ticket(session_factory: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    async with session_factory() as session:
        ticket = Ticket(event_id=uuid.uuid4(), section="A", row_name="1", seat_label="A1", status=TicketStatus.AVAILABLE)
        session.add(ticket)
        await session.commit()
        return ticket.id


async def test_cron_strategy_satisfies_the_shared_contract(db_session_factory: async_sessionmaker[AsyncSession]):
    # Same contract already proven fake-only in tests/unit/test_hold_strategy_contract.py
    # (P3.T3) — this proves CronHoldStrategy satisfies it too, against real Postgres.
    ticket_id = await _seed_ticket(db_session_factory)

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


async def test_redis_strategy_satisfies_the_shared_contract(redis_container):
    # Same contract, proven against real Redis — CronHoldStrategy and
    # RedisHoldStrategy satisfy the identical TicketHoldStrategy interface
    # despite storing hold state in entirely different places (§6).
    client = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(redis_container.port)),
        decode_responses=True,
    )
    ticket_id = uuid.uuid4()
    strategy = RedisHoldStrategy(client)

    assert await strategy.is_held(ticket_id) is False
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True
    assert await strategy.is_held(ticket_id) is True
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is False
    await strategy.release_hold(ticket_id)
    assert await strategy.is_held(ticket_id) is False

    await client.flushall()
    await client.aclose()
