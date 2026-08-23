import uuid

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base_repository import BaseRepository
from app.db.models import Payment, PaymentStatus


class PaymentRepository(BaseRepository[Payment]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Payment)

    async def get_by_booking_id(self, booking_id: uuid.UUID) -> Payment | None:
        result = await self._session.execute(select(Payment).where(Payment.booking_id == booking_id))
        return result.scalar_one_or_none()

    async def get_by_stripe_charge_id(self, stripe_charge_id: str) -> Payment | None:
        result = await self._session.execute(select(Payment).where(Payment.stripe_charge_id == stripe_charge_id))
        return result.scalar_one_or_none()

    async def transition_if_pending(self, stripe_charge_id: str, new_status: PaymentStatus) -> bool:
        """Rowcount-gated conditional UPDATE, same "only transition if
        currently in state X" shape as BookingRepository.transition_if_pending
        — a concurrent duplicate webhook delivery can't both win this, unlike
        a read-then-write version where two overlapping deliveries could both
        pass the PENDING check before either committed."""
        result = await self._session.execute(
            sa_update(Payment)
            .where(Payment.stripe_charge_id == stripe_charge_id, Payment.status == PaymentStatus.PENDING)
            .values(status=new_status)
        )
        await self._session.flush()
        return result.rowcount > 0

    async def transition_to_succeeded(self, stripe_charge_id: str) -> bool:
        """Also accepts FAILED, not just PENDING — a genuine success can
        arrive after an earlier failed webhook already parked the row in
        FAILED. Still idempotent: a row already SUCCEEDED matches neither
        branch."""
        result = await self._session.execute(
            sa_update(Payment)
            .where(
                Payment.stripe_charge_id == stripe_charge_id,
                Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.FAILED]),
            )
            .values(status=PaymentStatus.SUCCEEDED)
        )
        await self._session.flush()
        return result.rowcount > 0
