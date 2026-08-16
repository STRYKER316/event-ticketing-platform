import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core import get_session_factory, get_settings
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy

logger = structlog.get_logger()


async def _sweep_once() -> None:
    async with get_session_factory()() as session:
        released = await CronHoldStrategy(session).release_expired()
        await session.commit()
    if released:
        logger.info("hold_sweep_released_expired_holds", count=released)


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_sweep_once, "interval", seconds=get_settings().hold_sweep_interval_seconds)
    return scheduler
