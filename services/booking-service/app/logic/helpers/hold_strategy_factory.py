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
    # No else-raise: Settings.hold_strategy is Literal["cron", "redis"], so
    # pydantic-settings already rejects any other value at startup.
    strategy = get_settings().hold_strategy
    if strategy == "cron":
        return CronHoldStrategy(session)
    return RedisHoldStrategy(redis)
