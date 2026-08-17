import asyncio
import uuid

from app.logic.helpers.hold_strategy import TicketHoldStrategy


class FakeHoldStrategy(TicketHoldStrategy):
    """Trivial in-memory implementation — no Postgres/Redis required. For
    unit-testing layers above the strategy (BookingManager) without either
    real strategy running, per P3.T3's done-when criteria."""

    def __init__(self):
        self._held: set[uuid.UUID] = set()
        self._lock = asyncio.Lock()

    async def acquire_hold(self, ticket_id: uuid.UUID, ttl_seconds: int) -> bool:
        async with self._lock:
            if ticket_id in self._held:
                return False
            self._held.add(ticket_id)
            return True

    async def release_hold(self, ticket_id: uuid.UUID) -> None:
        async with self._lock:
            self._held.discard(ticket_id)

    async def is_held(self, ticket_id: uuid.UUID) -> bool:
        async with self._lock:
            return ticket_id in self._held

    async def confirm_hold(self, ticket_id: uuid.UUID) -> None:
        async with self._lock:
            self._held.discard(ticket_id)
