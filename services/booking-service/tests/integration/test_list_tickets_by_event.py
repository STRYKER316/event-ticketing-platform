import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository

pytestmark = pytest.mark.asyncio


async def test_list_by_event_returns_only_that_events_tickets_with_real_statuses(db_session: AsyncSession):
    event_id = uuid.uuid4()
    other_event_id = uuid.uuid4()
    session = db_session
    session.add_all(
        [
            Ticket(
                event_id=event_id,
                section="Floor",
                row_name="A",
                seat_label="1",
                price_cents=5000,
                status=TicketStatus.AVAILABLE,
            ),
            Ticket(
                event_id=event_id,
                section="Floor",
                row_name="A",
                seat_label="2",
                price_cents=5000,
                status=TicketStatus.HELD,
            ),
            Ticket(
                event_id=event_id,
                section="Balcony",
                row_name="B",
                seat_label="1",
                price_cents=3000,
                status=TicketStatus.BOOKED,
            ),
            Ticket(
                event_id=other_event_id,
                section="Floor",
                row_name="A",
                seat_label="1",
                price_cents=5000,
                status=TicketStatus.AVAILABLE,
            ),
        ]
    )
    await session.commit()

    tickets = await TicketRepository(session).list_by_event(event_id)

    assert len(tickets) == 3
    statuses = {(t.section, t.row_name, t.seat_label): t.status for t in tickets}
    assert statuses[("Floor", "A", "1")] == TicketStatus.AVAILABLE
    assert statuses[("Floor", "A", "2")] == TicketStatus.HELD
    assert statuses[("Balcony", "B", "1")] == TicketStatus.BOOKED
    assert all(t.event_id == event_id for t in tickets)


async def test_list_by_event_returns_empty_for_unknown_event(db_session: AsyncSession):
    tickets = await TicketRepository(db_session).list_by_event(uuid.uuid4())
    assert tickets == []
