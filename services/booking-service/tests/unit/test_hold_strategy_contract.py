import asyncio
import uuid

import pytest

from app.logic.helpers.fake_hold_strategy import FakeHoldStrategy

pytestmark = pytest.mark.asyncio


# The real Postgres/Redis implementations satisfy this same contract, proven against real infra in tests/integration/test_hold_strategy_contract.py; this file stays fake-only so it runs with no infra.
@pytest.fixture
def strategy() -> FakeHoldStrategy:
    return FakeHoldStrategy()


async def test_acquire_hold_on_available_ticket_succeeds(strategy):
    ticket_id = uuid.uuid4()
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True


async def test_is_held_reflects_active_hold(strategy):
    ticket_id = uuid.uuid4()
    assert await strategy.is_held(ticket_id) is False
    await strategy.acquire_hold(ticket_id, ttl_seconds=60)
    assert await strategy.is_held(ticket_id) is True


async def test_second_acquire_on_already_held_ticket_fails(strategy):
    ticket_id = uuid.uuid4()
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is False


async def test_release_then_reacquire_succeeds(strategy):
    ticket_id = uuid.uuid4()
    await strategy.acquire_hold(ticket_id, ttl_seconds=60)
    await strategy.release_hold(ticket_id)
    assert await strategy.is_held(ticket_id) is False
    assert await strategy.acquire_hold(ticket_id, ttl_seconds=60) is True


async def test_release_of_unheld_ticket_is_a_safe_no_op(strategy):
    ticket_id = uuid.uuid4()
    await strategy.release_hold(ticket_id)  # must not raise
    assert await strategy.is_held(ticket_id) is False


async def test_exactly_one_winner_under_concurrent_acquire(strategy):
    ticket_id = uuid.uuid4()
    results = await asyncio.gather(*(strategy.acquire_hold(ticket_id, ttl_seconds=60) for _ in range(20)))
    assert sum(results) == 1


async def test_different_tickets_do_not_interfere(strategy):
    ticket_a, ticket_b = uuid.uuid4(), uuid.uuid4()
    assert await strategy.acquire_hold(ticket_a, ttl_seconds=60) is True
    assert await strategy.acquire_hold(ticket_b, ttl_seconds=60) is True


async def test_confirm_hold_clears_tracking(strategy):
    ticket_id = uuid.uuid4()
    await strategy.acquire_hold(ticket_id, ttl_seconds=60)
    await strategy.confirm_hold(ticket_id)
    assert await strategy.is_held(ticket_id) is False


async def test_confirm_hold_of_unheld_ticket_is_a_safe_no_op(strategy):
    ticket_id = uuid.uuid4()
    await strategy.confirm_hold(ticket_id)  # must not raise
    assert await strategy.is_held(ticket_id) is False


async def test_release_booking_of_unbooked_ticket_is_a_safe_no_op(strategy):
    ticket_id = uuid.uuid4()
    await strategy.release_booking(ticket_id)  # must not raise
    assert await strategy.is_held(ticket_id) is False


async def test_release_booking_after_confirm_clears_tracking(strategy):
    ticket_id = uuid.uuid4()
    await strategy.acquire_hold(ticket_id, ttl_seconds=60)
    await strategy.confirm_hold(ticket_id)
    await strategy.release_booking(ticket_id)  # must not raise
    assert await strategy.is_held(ticket_id) is False
