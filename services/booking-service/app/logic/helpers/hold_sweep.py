import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core import get_session_factory, get_settings
from app.db.booking_repository import BookingRepository
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy

logger = structlog.get_logger()


async def _sweep_cron_holds_once() -> None:
    async with get_session_factory()() as session:
        released = await CronHoldStrategy(session).release_expired()
        await session.commit()
    if released:
        logger.info("hold_sweep_released_expired_holds", count=released)


async def _sweep_stale_redis_bookings_once() -> None:
    """Redis's own key expiry (§6) frees the seat for a new hold attempt, but
    never expires the abandoned PENDING Booking row it doesn't know about —
    see BookingRepository.expire_stale_pending()."""
    async with get_session_factory()() as session:
        expired = await BookingRepository(session).expire_stale_pending(get_settings().hold_ttl_seconds)
        await session.commit()
    if expired:
        logger.info("hold_sweep_expired_stale_redis_bookings", count=expired)


def build_scheduler() -> AsyncIOScheduler:
    """Both hold strategies need a periodic sweep — they just sweep different
    state (cron: tickets.status + hold_expires_at; redis: the Booking row
    age, since Redis expires its own hold state). An unrecognized
    HOLD_STRATEGY fails the same way here as get_hold_strategy() fails on the
    first request that needs it. No else-raise: Settings.hold_strategy is
    Literal["cron", "redis"], already rejected at startup otherwise."""
    strategy = get_settings().hold_strategy
    scheduler = AsyncIOScheduler()
    job = _sweep_cron_holds_once if strategy == "cron" else _sweep_stale_redis_bookings_once
    scheduler.add_job(job, "interval", seconds=get_settings().hold_sweep_interval_seconds)
    return scheduler
