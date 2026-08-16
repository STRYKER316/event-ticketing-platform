import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.logic.helpers.redis_hold_strategy import RedisHoldStrategy

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def redis_client(redis_container) -> Redis:
    client = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(redis_container.port)),
        decode_responses=True,
    )
    yield client
    await client.flushall()
    await client.aclose()


async def test_exactly_one_winner_under_concurrent_acquire(redis_client: Redis):
    ticket_id = uuid.uuid4()
    strategy = RedisHoldStrategy(redis_client)

    results = await asyncio.gather(*(strategy.acquire_hold(ticket_id, ttl_seconds=60) for _ in range(20)))
    assert sum(results) == 1


async def test_abandoned_hold_auto_releases_on_ttl_no_sweep_needed(redis_client: Redis):
    ticket_id = uuid.uuid4()
    strategy = RedisHoldStrategy(redis_client)

    assert await strategy.acquire_hold(ticket_id, ttl_seconds=1) is True
    assert await strategy.is_held(ticket_id) is True

    await asyncio.sleep(1.5)

    # No scheduler, no manual release call — Redis's own EX expiry is what
    # frees this. Proven by asking Redis directly, not via a scheduler run.
    assert await redis_client.exists(f"ticket:hold:{ticket_id}") == 0
    assert await strategy.is_held(ticket_id) is False
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True


async def test_explicit_release_then_reacquire(redis_client: Redis):
    ticket_id = uuid.uuid4()
    strategy = RedisHoldStrategy(redis_client)

    await strategy.acquire_hold(ticket_id, ttl_seconds=60)
    await strategy.release_hold(ticket_id)
    assert await strategy.is_held(ticket_id) is False
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True


async def test_release_of_unheld_ticket_is_a_safe_no_op(redis_client: Redis):
    ticket_id = uuid.uuid4()
    strategy = RedisHoldStrategy(redis_client)

    await strategy.release_hold(ticket_id)  # must not raise
    assert await strategy.is_held(ticket_id) is False
