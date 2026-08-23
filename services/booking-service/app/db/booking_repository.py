import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.models import Booking, BookingStatus


class BookingRepository(BaseRepository[Booking]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Booking)

    async def transition_if_pending(self, booking_id: uuid.UUID, new_status: BookingStatus) -> bool:
        """Idempotent by construction: only transitions a row currently in
        the expected state, so a redelivered message for an already-terminal
        booking matches zero rows and is a safe no-op (the general rule
        applied here to the payment-outcome consumer — decisions-log §7
        point #4/§21)."""
        return await self._transition_if_status(booking_id, BookingStatus.PENDING, new_status)

    async def transition_if_confirmed(self, booking_id: uuid.UUID, new_status: BookingStatus) -> bool:
        """Same rowcount-gated "only transition if currently in state X"
        shape as transition_if_pending, applied to cancellation (§22) — the
        race backstop against a concurrent duplicate cancel or an
        in-flight expiry sweep."""
        return await self._transition_if_status(booking_id, BookingStatus.CONFIRMED, new_status)

    async def _transition_if_status(
        self, booking_id: uuid.UUID, from_status: BookingStatus, new_status: BookingStatus
    ) -> bool:
        result = await self._session.execute(
            sa_update(Booking)
            .where(Booking.id == booking_id, Booking.status == from_status)
            .values(status=new_status)
        )
        await self._session.flush()
        return result.rowcount > 0

    async def list_confirmed_ticket_ids(self, event_id: uuid.UUID) -> set[uuid.UUID]:
        """Booking.status is the ground truth for BOOKED regardless of which
        TicketHoldStrategy is active — unlike tickets.status, which
        RedisHoldStrategy never writes at all (§6). Bulk query, not a
        per-ticket loop; used by BookingManager.list_tickets_for_event to
        determine BOOKED status strategy-independently."""
        result = await self._session.execute(
            select(Booking.ticket_id).where(Booking.event_id == event_id, Booking.status == BookingStatus.CONFIRMED)
        )
        return set(result.scalars().all())

    async def expire_stale_pending(self, older_than_seconds: int) -> int:
        """Age-based fallback for the RedisHoldStrategy (§6): Redis expires
        its own hold key on its own, but never touches this Booking row —
        without this, an abandoned PENDING booking sits forever and
        permanently blocks uq_bookings_active_ticket from ever allowing that
        seat to be booked again. Keyed off created_at vs the same
        hold_ttl_seconds the hold itself used, since there is no
        Ticket-side expiry column to join against under this strategy
        (unlike CronHoldStrategy.release_expired(), which has one)."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        result = await self._session.execute(
            sa_update(Booking)
            .where(Booking.status == BookingStatus.PENDING, Booking.created_at < cutoff)
            .values(status=BookingStatus.EXPIRED)
        )
        await self._session.flush()
        return result.rowcount
