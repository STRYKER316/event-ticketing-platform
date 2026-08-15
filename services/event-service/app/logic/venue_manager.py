import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import VenueCreate, VenueResponse
from app.db.models import Venue
from app.db.venue_repository import VenueRepository


class VenueManager:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._venues = VenueRepository(session)

    async def get_venue(self, venue_id: uuid.UUID) -> VenueResponse:
        venue = await self._venues.get_by_id(venue_id)
        if venue is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "venue not found")
        return VenueResponse.model_validate(venue)

    async def create_venue(self, payload: VenueCreate) -> VenueResponse:
        venue = Venue(name=payload.name, address=payload.address, capacity=payload.capacity)
        await self._venues.create(venue)
        await self._session.commit()
        return VenueResponse.model_validate(venue)
