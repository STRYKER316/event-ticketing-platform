import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Venue
from app.db.venue_repository import VenueRepository

pytestmark = pytest.mark.asyncio


async def test_delete_reports_false_when_the_row_is_already_gone(db_session: AsyncSession):
    repo = VenueRepository(db_session)
    venue = Venue(name="Deletable Venue", address="1 Test Way", capacity=100)
    db_session.add(venue)
    await db_session.commit()

    first = await repo.delete(venue.id)
    second = await repo.delete(venue.id)

    assert first is True
    assert second is False
