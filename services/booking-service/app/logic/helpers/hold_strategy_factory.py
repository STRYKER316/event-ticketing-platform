from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_settings
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy
from app.logic.helpers.hold_strategy import TicketHoldStrategy
from app.logic.helpers.redis_hold_strategy import RedisHoldStrategy


def get_hold_strategy(session: AsyncSession, redis: Redis) -> TicketHoldStrategy:
    """Config-switch (§6): HOLD_STRATEGY selects which implementation
    BookingManager and the Phase 8 benchmark harness run against, without
    either ever branching on which is active themselves."""
    strategy = get_settings().hold_strategy
    if strategy == "cron":
        return CronHoldStrategy(session)
    if strategy == "redis":
        return RedisHoldStrategy(redis)
    raise ValueError(f"unknown HOLD_STRATEGY: {strategy!r}")
