import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.chunking import chunked
from app.db.models import Ticket

# 7 bind params per row: the 6 supplied here (id, event_id, section,
# row_name, seat_label, price_cents) plus `status`, which isn't in this
# dict but is still a real bind param — Ticket.status's Python-side default
# (TicketStatus.AVAILABLE) is applied by SQLAlchemy at the Core level even
# for this values()-based bulk insert, so it counts. chunked()'s
# BIND_PARAM_SAFE_BATCH_SIZE is sized for exactly this call site's worst
# case (see its own docstring) — a seat map anywhere near event-service's
# MAX_SEAT_MAP_SEATS (20,000) would already overflow a single unbatched
# INSERT's ~32,767 bind-param cap without this chunking.


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
