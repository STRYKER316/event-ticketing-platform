import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PaymentStatus(enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey: booking_id/ticket_id belong to booking_db, a different service's database (database-per-service, §8).
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True, unique=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount_cents: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")
    status: Mapped[PaymentStatus] = mapped_column(nullable=False, default=PaymentStatus.PENDING)
    stripe_charge_id: Mapped[str | None] = mapped_column(String(255), index=True, unique=True)
    # Booking ID doubles as Stripe's idempotency key (§9) — stored explicitly, not re-derived, so a replayed charge is recognized without assuming the two stay identical.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # Resubmission gate for refund_payment (§22) — same "NULL means not yet submitted" role stripe_charge_id plays in create_charge.
    stripe_refund_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
