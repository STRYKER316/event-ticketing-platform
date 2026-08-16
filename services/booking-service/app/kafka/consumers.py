import json

import structlog
from aiokafka import AIOKafkaConsumer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import get_settings
from app.db.ticket_repository import TicketRepository
from app.kafka.schemas import EventUpsertedMessage, KafkaAction

logger = structlog.get_logger()


def build_kafka_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_id,
        auto_offset_reset="earliest",
    )


class ProvisioningConsumer:
    """No equivalent API route writes Ticket rows from a Kafka payload — this
    is the one place that does, so it calls TicketRepository directly rather
    than going through a Manager (per CLAUDE.md's layering convention for a
    consumer with no second entry point to unify with)."""

    def __init__(self, consumer: AIOKafkaConsumer, session_factory: async_sessionmaker[AsyncSession]):
        self._consumer = consumer
        self._session_factory = session_factory

    async def run(self) -> None:
        async for record in self._consumer:
            await self._handle(record.value)

    async def _handle(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw)
            # payload.get() assumes a JSON object; valid JSON that isn't one
            # (a bare list/string/number/null) raises AttributeError here,
            # which must not escape and kill the background consumer task.
            action = KafkaAction(payload.get("action"))
        except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
            logger.error("provisioning_consumer_message_unparseable", error=str(exc), raw=raw[:500])
            return

        if action is KafkaAction.DELETED:
            # event-service now refuses to delete a PUBLISHED event at all
            # (see EventManager._check_cannot_delete_published) precisely
            # because Booking Service may hold Ticket/Booking rows against
            # it — a DELETED message should never arrive for a provisioned
            # event in practice. Nothing to do here either way.
            return

        try:
            message = EventUpsertedMessage.model_validate(payload)
        except Exception:
            logger.error("provisioning_consumer_message_invalid", raw=raw[:500], exc_info=True)
            return

        # event-service only ever publishes an UPSERTED message when the event's
        # status is PUBLISHED (decisions-log §15 amendment, 2026-08-15) — every
        # message on this topic already represents a published event, so there is
        # no separate "is this published" check to make here.
        seats = [(seat.section, seat.row, seat.label) for seat in message.seats]
        async with self._session_factory() as session:
            inserted = await TicketRepository(session).bulk_upsert_available(message.event_id, seats)
            await session.commit()
        logger.info(
            "tickets_provisioned",
            event_id=str(message.event_id),
            seats_in_message=len(seats),
            tickets_inserted=inserted,
        )
