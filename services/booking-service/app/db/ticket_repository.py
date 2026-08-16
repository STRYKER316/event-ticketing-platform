import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.models import Ticket


class TicketRepository(BaseRepository[Ticket]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Ticket)

    async def bulk_upsert_available(self, event_id: uuid.UUID, seats: list[tuple[str, str, str]]) -> int:
        """Insert one AVAILABLE Ticket per (section, row, label) seat, in one
        statement. ON CONFLICT DO NOTHING against the (event_id, section,
        row_name, seat_label) unique constraint makes redelivery of the same
        provisioning message a safe no-op by construction (§7) — no
        pre-existence query needed, no per-row loop (never query inside a
        loop, per CLAUDE.md conventions)."""
        if not seats:
            return 0
        rows = [
            {"id": uuid.uuid4(), "event_id": event_id, "section": section, "row_name": row, "seat_label": label}
            for section, row, label in seats
        ]
        stmt = pg_insert(Ticket).values(rows).on_conflict_do_nothing(
            index_elements=["event_id", "section", "row_name", "seat_label"]
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount
