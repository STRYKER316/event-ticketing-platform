import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.chunking import chunked
from app.db.models import Ticket

# 7 bind params/row (6 here plus Ticket.status's SQLAlchemy-applied default) — chunked()'s batch size is sized for this call site's worst case.


class TicketRepository(BaseRepository[Ticket]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Ticket)

    async def bulk_upsert_available(self, event_id: uuid.UUID, seats: list[tuple[str, str, str, int]]) -> int:
        """Insert one AVAILABLE Ticket per (section, row, label, price_cents)
        seat, batched into fixed-size statements. ON CONFLICT DO NOTHING
        against the (event_id, section, row_name, seat_label) unique
        constraint makes redelivery of the same provisioning message a safe
        no-op by construction (§7) — no pre-existence query needed, no
        per-row loop (never query inside a loop, per CLAUDE.md conventions);
        batching by row count is a bind-param limit, not a per-row query."""
        if not seats:
            return 0
        rows = [
            {
                "id": uuid.uuid4(),
                "event_id": event_id,
                "section": section,
                "row_name": row,
                "seat_label": label,
                "price_cents": price_cents,
            }
            for section, row, label, price_cents in seats
        ]
        inserted = 0
        for batch in chunked(rows):
            stmt = pg_insert(Ticket).values(batch).on_conflict_do_nothing(
                index_elements=["event_id", "section", "row_name", "seat_label"]
            )
            result = await self._session.execute(stmt)
            inserted += result.rowcount
        await self._session.flush()
        return inserted

    async def list_by_event(self, event_id: uuid.UUID) -> list[Ticket]:
        """Every ticket for the event, regardless of status — the frontend's
        seat map (§23) joins this against Event Service's layout client-side
        to render live per-seat availability."""
        result = await self._session.execute(select(Ticket).where(Ticket.event_id == event_id))
        return list(result.scalars().all())
