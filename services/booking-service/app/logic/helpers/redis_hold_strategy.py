import uuid

from redis.asyncio import Redis

from app.logic.helpers.hold_strategy import TicketHoldStrategy


def _key(ticket_id: uuid.UUID) -> str:
    return f"ticket:hold:{ticket_id}"


class RedisHoldStrategy(TicketHoldStrategy):
    """Hold state lives only in Redis (§6) — deliberately never writes
    tickets.status, unlike CronHoldStrategy. Redis's own SET NX EX is the
    entire correctness mechanism for the hold itself; there is no Postgres
    write to keep in sync at acquire time. Consequence, documented rather
    than hidden: an abandoned hold under this strategy leaves tickets.status
    at AVAILABLE for its whole lifetime — nothing here ever claims otherwise.
    Anything needing live hold status must call is_held() on whichever
    strategy is actually active, never read tickets.status directly, since
    that column only reflects hold state under the cron strategy. The
    Booking row this strategy knows nothing about still needs sweeping —
    see hold_sweep.py's redis-specific job and BookingRepository.expire_stale_pending()."""

    def __init__(self, redis: Redis):
        self._redis = redis

    async def acquire_hold(self, ticket_id: uuid.UUID, ttl_seconds: int) -> bool:
        acquired = await self._redis.set(_key(ticket_id), "1", nx=True, ex=ttl_seconds)
        return bool(acquired)

    async def release_hold(self, ticket_id: uuid.UUID) -> None:
        await self._redis.delete(_key(ticket_id))

    async def is_held(self, ticket_id: uuid.UUID) -> bool:
        return await self._redis.exists(_key(ticket_id)) > 0

    async def confirm_hold(self, ticket_id: uuid.UUID) -> None:
        # Ticket.status is never written under this strategy (see class
        # docstring) — confirming just cleans up the now-superseded Redis
        # hold key rather than leaving it to sit until its own TTL expiry.
        await self._redis.delete(_key(ticket_id))
