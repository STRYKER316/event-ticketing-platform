import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog
from aiokafka import AIOKafkaConsumer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import get_settings
from app.db.payment_repository import PaymentRepository
from app.kafka.producers import get_notification_producer
from app.kafka.schemas import BookingCancelledMessage
from app.logic.payment_manager import PaymentManager

logger = structlog.get_logger()

# Same shape as booking-service/app/kafka/consumers.py's own constants — a
# transient DB error (connection blip, pool exhaustion) is retried in place
# a few times before this consumer gives up on a message. Duplicated here
# rather than shared, since these are two independently deployable
# services (same reasoning Kafka schemas are independently defined on each
# side, not shared code).
DB_WRITE_MAX_ATTEMPTS = 3
DB_WRITE_RETRY_BACKOFF_SECONDS = 1.0

_T = TypeVar("_T")


async def _retry_with_backoff(
    operation: Callable[[], Awaitable[_T]],
    *,
    max_attempts: int,
    backoff_seconds: float,
    retrying_event: str,
    failed_event: str,
    **log_context: object,
) -> _T | None:
    """Generic bounded-retry shape, split out from the DB-specific wrapper
    below (mirrors booking-service/app/kafka/consumers.py's own split) —
    _refund's operation is a Kafka publish and a Stripe call too, not just
    a DB write."""
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
    # Unconditional commit() at the end, same as booking-service's copy of
    # this helper — kept even though this service's only current caller
    # (_refund) routes through PaymentManager, which already owns its own
    # commit per this project's "Manager methods that mutate always end
    # with commit()" convention, making this a harmless no-op today.
    # Removing it would silently strand any *future* handler wired through
    # this same helper that doesn't route through a self-committing Manager,
    # with no signal that anything was wrong — a safety net worth the
    # redundant call.
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
    async for record in consumer:
        await handle(record.value)
        await consumer.commit()


def build_cancelled_bookings_consumer() -> AIOKafkaConsumer:
    settings = get_settings()
    return AIOKafkaConsumer(
        settings.cancelled_bookings_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.cancelled_bookings_consumer_group_id,
        auto_offset_reset="earliest",
        # Same reasoning as booking-service's consumers — manual, per-record
        # offset commit only after _handle() has fully finished, so a
        # process crash mid-refund is always safely redelivered.
        enable_auto_commit=False,
    )


class BookingCancelledConsumer:
    """This service's first-ever Kafka consumer (§22, integration point
    #5). No equivalent API route triggers a refund from a Kafka payload —
    goes straight to PaymentManager.refund_payment, same as
    ProvisioningConsumer's own reasoning for calling its repository
    directly when there's no second entry point to unify with."""

    def __init__(self, consumer: AIOKafkaConsumer, session_factory: async_sessionmaker[AsyncSession]):
        self._consumer = consumer
        self._session_factory = session_factory

    async def run(self) -> None:
        await _consume_with_manual_commit(self._consumer, self._handle)

    async def _handle(self, raw: bytes) -> None:
        try:
            message = BookingCancelledMessage.model_validate_json(raw)
        except Exception:
            logger.error("booking_cancelled_consumer_message_invalid", raw=raw[:500], exc_info=True)
            return

        await self._refund_with_retry(message.booking_id)

    async def _refund_with_retry(self, booking_id: uuid.UUID) -> None:
        """Retries a transient failure before giving up — see _run_with_retry().
        A Stripe-side failure is already handled inside refund_payment() and
        doesn't propagate here, so this only re-runs on a genuine DB or
        producer-lookup error. A retried Stripe call is still safe: same
        idempotency_key, no double refund (see refund_payment's docstring)."""

        async def _refund(session: AsyncSession) -> None:
            manager = PaymentManager(session=session, payments=PaymentRepository(session))
            notification_producer = await get_notification_producer()
            await manager.refund_payment(booking_id, notification_producer)

        await _run_with_retry(
            self._session_factory,
            _refund,
            retrying_event="booking_cancelled_consumer_refund_processing_failed_retrying",
            failed_event="booking_cancelled_consumer_refund_processing_failed_permanently",
            booking_id=str(booking_id),
        )
