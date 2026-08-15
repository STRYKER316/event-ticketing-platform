from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.models import Performer


class PerformerRepository(BaseRepository[Performer]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Performer)
