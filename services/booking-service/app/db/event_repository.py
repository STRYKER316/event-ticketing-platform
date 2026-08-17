import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event


class EventRepository:
    """Not a BaseRepository subclass — Event's primary key is event_id, not
    id, and this table has exactly one write path (ProvisioningConsumer)
    and one read path (the cancellation cutoff check), neither of which
    needs BaseRepository's generic create/get/delete shape."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert_start_time(self, event_id: uuid.UUID, start_time: datetime) -> None:
        """A republished event with a corrected start_time stays current
        rather than sticking to whatever value arrived first (§22
        amendment #2) — same idempotent-upsert shape as
        TicketRepository.bulk_upsert_available, but ON CONFLICT DO UPDATE
        instead of DO NOTHING since this column can legitimately change."""
        stmt = pg_insert(Event).values(event_id=event_id, start_time=start_time)
        stmt = stmt.on_conflict_do_update(index_elements=["event_id"], set_={"start_time": stmt.excluded.start_time})
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_start_time(self, event_id: uuid.UUID) -> datetime | None:
        event = await self._session.get(Event, event_id)
        return event.start_time if event is not None else None
