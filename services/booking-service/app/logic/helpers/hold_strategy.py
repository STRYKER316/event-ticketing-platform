import uuid
from abc import ABC, abstractmethod


class TicketHoldStrategy(ABC):
    """§6: both hold mechanisms (cron sweep, Redis TTL) satisfy this same
    contract so BookingManager and the Phase 8 benchmark harness never
    branch on which is active. Hold-state storage is deliberately
    asymmetric between the two concrete implementations even though the
    contract they satisfy is identical — see CronHoldStrategy's and
    RedisHoldStrategy's own docstrings."""

    @abstractmethod
    async def acquire_hold(self, ticket_id: uuid.UUID, ttl_seconds: int) -> bool:
        """Attempt to acquire a hold on ticket_id. Returns True if acquired,
        False if already held. Must be atomic under concurrent callers —
        exactly one caller may receive True for a given ticket_id."""

    @abstractmethod
    async def release_hold(self, ticket_id: uuid.UUID) -> None:
        """Release a hold. Idempotent — releasing an unheld ticket is a safe no-op."""

    @abstractmethod
    async def is_held(self, ticket_id: uuid.UUID) -> bool:
        """Return whether ticket_id currently has an active, unexpired hold."""

    @abstractmethod
    async def confirm_hold(self, ticket_id: uuid.UUID) -> None:
        """Mark a hold as fulfilled (payment succeeded, §21) rather than
        released back to available — the seat is now permanently booked, not
        up for grabs again. Idempotent, same contract as release_hold."""
