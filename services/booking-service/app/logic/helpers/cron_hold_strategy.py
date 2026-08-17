import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.chunking import chunked
from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.logic.helpers.hold_strategy import TicketHoldStrategy


class CronHoldStrategy(TicketHoldStrategy):
    """Hold state IS tickets.status/hold_expires_at (§6) — the Redis
    strategy never writes these columns at all (see its own docstring).
    Correctness comes from one atomic conditional UPDATE per operation, the
    same TOCTOU-safe pattern as BaseRepository.delete()'s rowcount-checked
    Core DELETE: two concurrent transactions targeting the same row
    serialize on Postgres's row lock, and the loser's WHERE clause
    re-evaluates false once the winner has committed."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def acquire_hold(self, ticket_id: uuid.UUID, ttl_seconds: int) -> bool:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        result = await self._session.execute(
            sa_update(Ticket)
            .where(Ticket.id == ticket_id, Ticket.status == TicketStatus.AVAILABLE)
            .values(status=TicketStatus.HELD, hold_expires_at=expires_at)
        )
        await self._session.flush()
        return result.rowcount > 0

    async def release_hold(self, ticket_id: uuid.UUID) -> None:
        await self._session.execute(
            sa_update(Ticket)
            .where(Ticket.id == ticket_id, Ticket.status == TicketStatus.HELD)
            .values(status=TicketStatus.AVAILABLE, hold_expires_at=None)
        )
        await self._session.flush()

    async def confirm_hold(self, ticket_id: uuid.UUID) -> None:
        await self._session.execute(
            sa_update(Ticket)
            .where(Ticket.id == ticket_id, Ticket.status == TicketStatus.HELD)
            .values(status=TicketStatus.BOOKED, hold_expires_at=None)
        )
        await self._session.flush()

    async def is_held(self, ticket_id: uuid.UUID) -> bool:
        ticket = await self._session.get(Ticket, ticket_id)
        if ticket is None or ticket.status is not TicketStatus.HELD:
            return False
        return ticket.hold_expires_at is not None and ticket.hold_expires_at > datetime.now(timezone.utc)

    async def release_expired(self) -> int:
        """Periodic sweep (APScheduler, see hold_sweep.py) — not part of the
        TicketHoldStrategy ABC; the Redis strategy has no analog since Redis
        expires keys on its own (§6). Expires PENDING Bookings for tickets
        about to be released BEFORE releasing the tickets, using the same
        expiry predicate for both statements — reversing the order would let
        the ticket UPDATE's own effect (status flips away from HELD) make
        the booking UPDATE's subquery match nothing."""
        now = datetime.now(timezone.utc)
        expiring_ticket_ids = (
            (
                await self._session.execute(
                    select(Ticket.id).where(Ticket.status == TicketStatus.HELD, Ticket.hold_expires_at < now)
                )
            )
            .scalars()
            .all()
        )
        if not expiring_ticket_ids:
            return 0

        released = 0
        for batch in chunked(expiring_ticket_ids):
            # Both UPDATEs below re-check status/expiry, not just ticket ID: a
            # ticket in this batch may have been re-held or booked between the
            # SELECT above and here, and a bare-ID UPDATE would then wrongly
            # touch a row that has since moved on — the exact TOCTOU gap this
            # class's docstring claims not to have. Both statements use the
            # identical fresh predicate so they agree on exactly the same set
            # of tickets, still-expired as of right now.
            still_expired_ids = select(Ticket.id).where(
                Ticket.id.in_(batch), Ticket.status == TicketStatus.HELD, Ticket.hold_expires_at < now
            )
            await self._session.execute(
                sa_update(Booking)
                .where(Booking.ticket_id.in_(still_expired_ids), Booking.status == BookingStatus.PENDING)
                .values(status=BookingStatus.EXPIRED)
            )
            result = await self._session.execute(
                sa_update(Ticket)
                .where(
                    Ticket.id.in_(batch),
                    Ticket.status == TicketStatus.HELD,
                    Ticket.hold_expires_at < now,
                )
                .values(status=TicketStatus.AVAILABLE, hold_expires_at=None)
            )
            released += result.rowcount
        await self._session.flush()
        return released
