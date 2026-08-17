import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from shared_auth import Principal
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.community.kafka import KafkaContainer

from app.db.booking_repository import BookingRepository
from app.db.event_repository import EventRepository
from app.db.models import Booking, BookingStatus, Event, Ticket, TicketStatus
from app.db.ticket_repository import TicketRepository
from app.kafka.producers import BookingCancelledProducer
from app.logic.booking_manager import BookingManager
from app.logic.helpers.cron_hold_strategy import CronHoldStrategy

pytestmark = pytest.mark.asyncio

TOPIC = "booking.cancelled.test"
USER = Principal(subject="user-1", roles=[])


async def _seed_confirmed_booking(session_factory: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_factory() as session:
        event_id = uuid.uuid4()
        session.add(Event(event_id=event_id, start_time=datetime.now(timezone.utc) + timedelta(hours=2)))
        ticket = Ticket(
            event_id=event_id, section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.BOOKED
        )
        session.add(ticket)
        await session.flush()
        booking = Booking(user_subject=USER.subject, event_id=event_id, ticket_id=ticket.id, status=BookingStatus.CONFIRMED)
        session.add(booking)
        await session.commit()
        return booking.id, ticket.id


async def test_cancel_booking_publishes_a_real_message_on_the_real_topic(
    db_session_factory: async_sessionmaker[AsyncSession], kafka_container: "KafkaContainer"
):
    # Proves BookingCancelledProducer's wire format against a real broker —
    # the unit/mocked tests already prove cancel_booking's business logic,
    # this proves the Kafka-transport half (§22, integration point #5).
    bootstrap_servers = kafka_container.get_bootstrap_server()
    booking_id, ticket_id = await _seed_confirmed_booking(db_session_factory)

    producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
    await producer.start()
    consumer = AIOKafkaConsumer(
        TOPIC, bootstrap_servers=bootstrap_servers, auto_offset_reset="earliest", enable_auto_commit=False
    )
    await consumer.start()
    try:
        async with db_session_factory() as session:
            manager = BookingManager(
                session=session,
                tickets=TicketRepository(session),
                bookings=BookingRepository(session),
                hold_strategy=CronHoldStrategy(session),
                events=EventRepository(session),
                cancelled_producer=BookingCancelledProducer(producer, TOPIC),
            )
            result = await manager.cancel_booking(USER, booking_id)

        assert result.status is BookingStatus.CANCELLED

        record = await asyncio.wait_for(consumer.getone(), timeout=15)
        payload = json.loads(record.value)
        assert payload == {"booking_id": str(booking_id)}
        assert record.key == str(booking_id).encode()

        async with db_session_factory() as session:
            ticket = await session.get(Ticket, ticket_id)
            assert ticket.status is TicketStatus.AVAILABLE
            booking = await session.get(Booking, booking_id)
            assert booking.status is BookingStatus.CANCELLED
    finally:
        await producer.stop()
        await consumer.stop()
