import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TicketStatus(enum.Enum):
    AVAILABLE = "available"
    HELD = "held"
    BOOKED = "booked"


class BookingStatus(enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("event_id", "section", "row_name", "seat_label", name="uq_ticket_event_seat"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ForeignKey: event_id belongs to event_db, a different service's database
    # (database-per-service, §8) — cross-service data only arrives via Kafka.
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    section: Mapped[str] = mapped_column(String(100), nullable=False)
    row_name: Mapped[str] = mapped_column(String(100), nullable=False)
    seat_label: Mapped[str] = mapped_column(String(100), nullable=False)
    # Organizer-set per section at provisioning time (decisions-log §9/§16
    # amendments, 2026-08-17) — what Payment Service charges against.
    price_cents: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[TicketStatus] = mapped_column(nullable=False, default=TicketStatus.AVAILABLE, index=True)
    # Cron strategy's own hold state (§6) — the Redis strategy never writes this
    # column; see the Redis strategy's docstring for why that split is deliberate.
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Event(Base):
    """Minimal reference row, not a copy of Event Service's own data (§8 —
    that would be cross-service table duplication beyond what's needed
    here). Written by ProvisioningConsumer from the same Kafka message
    that already provisions Ticket rows (decisions-log §22 amendment #2)
    — booking_db's only use for it is enforcing the cancellation cutoff
    (§22), nothing else reads it."""

    __tablename__ = "events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # No ForeignKey, same reasoning as Ticket.event_id.
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(nullable=False, default=BookingStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
