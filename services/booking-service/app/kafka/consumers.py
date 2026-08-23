import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TypeVar

import structlog
from aiokafka import AIOKafkaConsumer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import get_settings
from app.db.booking_repository import BookingRepository
from app.db.event_repository import EventRepository
from app.db.models import BookingStatus
from app.db.ticket_repository import TicketRepository
from app.kafka.producers import BookingCancelledProducer, NotificationProducer
from app.kafka.schemas import EventUpsertedMessage, KafkaAction, PaymentOutcomeAction, PaymentOutcomeMessage
from app.logic.helpers.hold_strategy_factory import get_hold_strategy

logger = structlog.get_logger()

# Retried so a transient DB error doesn't let the per-record offset commit advance past a message whose write never actually succeeded.
DB_WRITE_MAX_ATTEMPTS = 3
DB_WRITE_RETRY_BACKOFF_SECONDS = 1.0

_T = TypeVar("_T")

# Same retry shape as _run_with_retry, applied to a Kafka send instead of a DB write.
NOTIFICATION_PUBLISH_MAX_ATTEMPTS = 3
NOTIFICATION_PUBLISH_RETRY_BACKOFF_SECONDS = 1.0


async def _retry_with_backoff(
    operation: Callable[[], Awaitable[_T]],
    *,
    max_attempts: int,
    backoff_seconds: float,
    retrying_event: str,
    failed_event: str,
    **log_context: object,
) -> _T | None:
    """Retries a transient failure in place with backoff, logging critical
    (not silently) on the last attempt rather than raising.
    `_run_with_retry` below wraps a DB write in a session/commit;
    PaymentOutcomeConsumer's notification publish uses this directly with
    no session at all."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception:
            if attempt == max_attempts:
                logger.critical(failed_event, attempts=attempt, exc_info=True, **log_context)
                return None
            logger.warning(retrying_event, attempt=attempt, exc_info=True, **log_context)
            await asyncio.sleep(backoff_seconds)
    return None


async def _run_with_retry(
    session_factory: async_sessionmaker[AsyncSession],
    operation: Callable[[AsyncSession], Awaitable[_T]],
    *,
    retrying_event: str,
    failed_event: str,
    **log_context: object,
) -> _T | None:
    """Shared by ProvisioningConsumer and PaymentOutcomeConsumer: runs
    `operation` against a fresh session and commits, retrying a transient DB
    failure DB_WRITE_MAX_ATTEMPTS times with backoff before giving up — see
    DB_WRITE_MAX_ATTEMPTS's module docstring for why this exists. Returns
    None only once every attempt has failed, at which point the caller
    commits the Kafka offset anyway and moves on (logged at critical, not
    silently)."""

    async def _in_session() -> _T:
        async with session_factory() as session:
            result = await operation(session)
            await session.commit()
            return result

    return await _retry_with_backoff(
        _in_session,
        max_attempts=DB_WRITE_MAX_ATTEMPTS,
        backoff_seconds=DB_WRITE_RETRY_BACKOFF_SECONDS,
        retrying_event=retrying_event,
        failed_event=failed_event,
        **log_context,
    )


async def _consume_with_manual_commit(consumer: AIOKafkaConsumer, handle: Callable[[bytes], Awaitable[None]]) -> None:
    """Shared by ProvisioningConsumer.run() and PaymentOutcomeConsumer.run() —
    commits only after `handle` has fully finished with the record, see
    build_kafka_consumer()'s enable_auto_commit note."""
    async for record in consumer:
        await handle(record.value)
        await consumer.commit()


def build_kafka_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_id,
        auto_offset_reset="earliest",
        # Manual, per-record commit after _handle() finishes — default auto-commit could advance past a write that never actually succeeded.
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
        await _consume_with_manual_commit(self._consumer, self._handle)

    async def _handle(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw)
            # payload.get() assumes a JSON object; valid non-object JSON raises AttributeError, caught below rather than killing the consumer task.
            action = KafkaAction(payload.get("action"))
        except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
            logger.error("provisioning_consumer_message_unparseable", error=str(exc), raw=raw[:500])
            return

        if action is KafkaAction.DELETED:
            # event-service refuses to delete a PUBLISHED event (see EventManager._check_cannot_delete_published), so this should never arrive.
            return

        try:
            message = EventUpsertedMessage.model_validate(payload)
        except Exception:
            logger.error("provisioning_consumer_message_invalid", raw=raw[:500], exc_info=True)
            return

        # event-service only ever publishes UPSERTED for a PUBLISHED event (§15 amendment) — no separate "is this published" check needed here.
        seats = [(seat.section, seat.row, seat.label, seat.price_cents) for seat in message.seats]
        inserted = await self._write_tickets(message.event_id, message.start_time, seats)
        if inserted is None:
            return
        logger.info(
            "tickets_provisioned",
            event_id=str(message.event_id),
            seats_in_message=len(seats),
            tickets_inserted=inserted,
        )

    async def _write_tickets(
        self, event_id: uuid.UUID, start_time: datetime, seats: list[tuple[str, str, str, int]]
    ) -> int | None:
        """Retries a transient DB failure in place before giving up — see
        _run_with_retry(). An unhandled exception here would escape run()'s
        consume loop and kill the consumer task for good, silently stopping
        provisioning for every future event too, which is worse than losing
        this one — same trade-off search-service's EventConsumer makes for
        its own DB write. Also upserts the Event reference row (§22
        amendment #2) in the same transaction as the ticket write, since
        both come from the one message."""

        async def _write(session: AsyncSession) -> int:
            await EventRepository(session).upsert_start_time(event_id, start_time)
            return await TicketRepository(session).bulk_upsert_available(event_id, seats)

        return await _run_with_retry(
            self._session_factory,
            _write,
            retrying_event="provisioning_consumer_db_write_failed_retrying",
            failed_event="provisioning_consumer_db_write_failed_permanently",
            event_id=str(event_id),
        )


def build_payment_outcome_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.payment_outcomes_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.payment_outcome_consumer_group_id,
        auto_offset_reset="earliest",
        # Same reasoning as build_kafka_consumer() above.
        enable_auto_commit=False,
    )


_STATUS_BY_ACTION = {
    PaymentOutcomeAction.SUCCEEDED: BookingStatus.CONFIRMED,
    PaymentOutcomeAction.FAILED: BookingStatus.EXPIRED,
}


class PaymentOutcomeConsumer:
    """No equivalent API route drives a PENDING booking to CONFIRMED or
    EXPIRED from a Kafka payload — goes straight to the repository/hold
    strategy rather than through BookingManager, same reasoning
    ProvisioningConsumer's own docstring gives for its own direct-repository
    call. Confirms or releases based on Payment Service's webhook-driven
    outcome (integration point #4, §17, §21) — reuses the exact
    TicketHoldStrategy methods BookingManager's own compensation path and the
    cron/Redis sweeps already use, not a third release/confirm mechanism."""

    def __init__(
        self,
        consumer: AIOKafkaConsumer,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        notification_producer: NotificationProducer,
        cancelled_producer: BookingCancelledProducer,
    ):
        self._consumer = consumer
        self._session_factory = session_factory
        self._redis = redis
        self._notification_producer = notification_producer
        self._cancelled_producer = cancelled_producer

    async def run(self) -> None:
        await _consume_with_manual_commit(self._consumer, self._handle)

    async def _handle(self, raw: bytes) -> None:
        try:
            message = PaymentOutcomeMessage.model_validate_json(raw)
        except Exception:
            logger.error("payment_outcome_consumer_message_invalid", raw=raw[:500], exc_info=True)
            return

        new_status = _STATUS_BY_ACTION[message.action]
        transitioned = await self._transition_with_retry(message, new_status)
        if transitioned:
            logger.info(
                "payment_outcome_applied",
                booking_id=str(message.booking_id),
                action=message.action.value,
            )

    async def _transition_with_retry(self, message: PaymentOutcomeMessage, new_status: BookingStatus) -> bool | None:
        """Retries a transient DB failure in place before giving up — same
        shape as ProvisioningConsumer._write_tickets(), see _run_with_retry().

        The notification publish stays outside this retried unit: this
        consumer has no redelivery mechanism above it, so a publish failure
        inside the transaction would roll back an already-committed DB
        transition instead of just failing to notify."""

        async def _transition(session: AsyncSession) -> tuple[bool, BookingStatus | None]:
            bookings = BookingRepository(session)
            transitioned = await bookings.transition_if_pending(message.booking_id, new_status)
            if transitioned:
                # Only touch the hold strategy if this call actually won the transition — a redelivered no-op must not touch a later booking's hold.
                strategy = get_hold_strategy(session, self._redis)
                if message.action is PaymentOutcomeAction.SUCCEEDED:
                    await strategy.confirm_hold(message.ticket_id)
                else:
                    await strategy.release_hold(message.ticket_id)
                return transitioned, None
            if message.action is PaymentOutcomeAction.SUCCEEDED:
                # Lost the transition on SUCCEEDED: likely a hold-expiry sweep already flipped this to EXPIRED — look up status so the caller can refund.
                current = await bookings.get_by_id(message.booking_id)
                return transitioned, current.status if current is not None else None
            return transitioned, None

        result = await _run_with_retry(
            self._session_factory,
            _transition,
            retrying_event="payment_outcome_consumer_db_write_failed_retrying",
            failed_event="payment_outcome_consumer_db_write_failed_permanently",
            booking_id=str(message.booking_id),
        )
        if result is None:
            return None
        transitioned, current_status = result

        if transitioned and message.action is PaymentOutcomeAction.SUCCEEDED:
            # Integration point #3 (§7 point 3) — best-effort; the CONFIRMED status has already committed regardless of whether this succeeds.
            await self._publish_confirmation_with_retry(message.booking_id)
        elif not transitioned and current_status is BookingStatus.EXPIRED:
            # Charged with nothing to show for it: reuse booking.cancelled (integration point #5, §22) to refund rather than a second compensation mechanism (§8). No DLQ here — retry exhaustion drops the refund trigger (decisions-log §26).
            logger.warning(
                "payment_outcome_succeeded_on_expired_booking_triggering_refund",
                booking_id=str(message.booking_id),
            )
            await self._trigger_refund_with_retry(message.booking_id)

        return transitioned

    async def _publish_confirmation_with_retry(self, booking_id: uuid.UUID) -> None:
        async def _publish() -> None:
            await self._notification_producer.publish_booking_confirmed(booking_id)

        await _retry_with_backoff(
            _publish,
            max_attempts=NOTIFICATION_PUBLISH_MAX_ATTEMPTS,
            backoff_seconds=NOTIFICATION_PUBLISH_RETRY_BACKOFF_SECONDS,
            retrying_event="payment_outcome_consumer_notification_publish_failed_retrying",
            failed_event="payment_outcome_consumer_notification_publish_failed_permanently",
            booking_id=str(booking_id),
        )

    async def _trigger_refund_with_retry(self, booking_id: uuid.UUID) -> None:
        async def _publish() -> None:
            await self._cancelled_producer.publish_cancelled(booking_id)

        await _retry_with_backoff(
            _publish,
            max_attempts=NOTIFICATION_PUBLISH_MAX_ATTEMPTS,
            backoff_seconds=NOTIFICATION_PUBLISH_RETRY_BACKOFF_SECONDS,
            retrying_event="payment_outcome_consumer_refund_trigger_publish_failed_retrying",
            failed_event="payment_outcome_consumer_refund_trigger_publish_failed_permanently",
            booking_id=str(booking_id),
        )
