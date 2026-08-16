import asyncio
import json
import uuid

import structlog
from aiokafka import AIOKafkaConsumer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import get_settings
from app.db.ticket_repository import TicketRepository
from app.kafka.schemas import EventUpsertedMessage, KafkaAction

logger = structlog.get_logger()

# A transient DB error (connection blip, pool exhaustion, brief deadlock) is
# retried in place a few times before this consumer gives up on a message —
# without this, run()'s per-record offset commit (see enable_auto_commit
# below) would advance straight past a message whose write never actually
# succeeded, silently losing that event's tickets on the very first hiccup.
DB_WRITE_MAX_ATTEMPTS = 3
DB_WRITE_RETRY_BACKOFF_SECONDS = 1.0


def build_kafka_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_id,
        auto_offset_reset="earliest",
        # Default (True) commits offsets on a background timer regardless of
        # whether _handle()'s DB write actually finished — a crash between
        # that timer firing and the write committing would silently drop
        # tickets instead of safely redelivering them (§7 idempotency relies
        # on redelivery actually happening). Committing manually, once per
        # record, after _handle() returns, guarantees a genuine process
        # crash mid-write is always safely redelivered. It does not by
        # itself guarantee a *caught* write failure is retried forever —
        # see DB_WRITE_MAX_ATTEMPTS in _handle() for that half of the story.
        enable_auto_commit=False,
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
            # Commit only after _handle() has fully finished with this
            # record — see build_kafka_consumer()'s enable_auto_commit note.
            await self._consumer.commit()

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
        inserted = await self._write_tickets(message.event_id, seats)
        if inserted is None:
            return
        logger.info(
            "tickets_provisioned",
            event_id=str(message.event_id),
            seats_in_message=len(seats),
            tickets_inserted=inserted,
        )

    async def _write_tickets(self, event_id: uuid.UUID, seats: list[tuple[str, str, str]]) -> int | None:
        """Retries a transient DB failure in place before giving up — see
        DB_WRITE_MAX_ATTEMPTS's module-level docstring for why this exists.
        Returns None only once every attempt has failed, at which point the
        caller commits the Kafka offset anyway and moves on: an unhandled
        exception here would escape run()'s `async for` loop and kill the
        consumer task for good, silently stopping provisioning for every
        future event too, which is worse than losing this one (logged at
        critical, not silently) — same trade-off search-service's
        EventConsumer makes for its own DB write."""
        for attempt in range(1, DB_WRITE_MAX_ATTEMPTS + 1):
            try:
                async with self._session_factory() as session:
                    inserted = await TicketRepository(session).bulk_upsert_available(event_id, seats)
                    await session.commit()
                return inserted
            except Exception:
                if attempt == DB_WRITE_MAX_ATTEMPTS:
                    logger.critical(
                        "provisioning_consumer_db_write_failed_permanently",
                        event_id=str(event_id),
                        attempts=attempt,
                        exc_info=True,
                    )
                    return None
                logger.warning(
                    "provisioning_consumer_db_write_failed_retrying",
                    event_id=str(event_id),
                    attempt=attempt,
                    exc_info=True,
                )
                await asyncio.sleep(DB_WRITE_RETRY_BACKOFF_SECONDS)
        return None
