import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Table, Column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class EventStatus(enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


event_performers = Table(
    "event_performers",
    Base.metadata,
    Column("event_id", UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
    Column("performer_id", UUID(as_uuid=True), ForeignKey("performers.id", ondelete="CASCADE"), primary_key=True),
)


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    capacity: Mapped[int] = mapped_column(nullable=False)

    events: Mapped[list["Event"]] = relationship(back_populates="venue")


class Performer(Base):
    __tablename__ = "performers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    bio: Mapped[str | None] = mapped_column(String(2000))

    events: Mapped[list["Event"]] = relationship(secondary=event_performers, back_populates="performers")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(5000))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[EventStatus] = mapped_column(nullable=False, default=EventStatus.DRAFT)
    organizer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    venue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("venues.id"), nullable=False)

    venue: Mapped["Venue"] = relationship(back_populates="events")
    performers: Mapped[list["Performer"]] = relationship(secondary=event_performers, back_populates="events")
