import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Ticket, TicketStatus
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy

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
