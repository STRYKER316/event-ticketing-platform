import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.models import Payment


class PaymentRepository(BaseRepository[Payment]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Payment)

    async def get_by_booking_id(self, booking_id: uuid.UUID) -> Payment | None:
        result = await self._session.execute(select(Payment).where(Payment.booking_id == booking_id))
        return result.scalar_one_or_none()

    async def get_by_stripe_charge_id(self, stripe_charge_id: str) -> Payment | None:
        result = await self._session.execute(select(Payment).where(Payment.stripe_charge_id == stripe_charge_id))
        return result.scalar_one_or_none()
