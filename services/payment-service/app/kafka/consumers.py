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


async def _run_with_retry(
    session_factory: async_sessionmaker[AsyncSession],
    operation: Callable[[AsyncSession], Awaitable[_T]],
    *,
    retrying_event: str,
    failed_event: str,
    **log_context: object,
) -> _T | None:
    for attempt in range(1, DB_WRITE_MAX_ATTEMPTS + 1):
        try:
            async with session_factory() as session:
                result = await operation(session)
                await session.commit()
            return result
        except Exception:
            if attempt == DB_WRITE_MAX_ATTEMPTS:
                logger.critical(failed_event, attempts=attempt, exc_info=True, **log_context)
                return None
            logger.warning(retrying_event, attempt=attempt, exc_info=True, **log_context)
            await asyncio.sleep(DB_WRITE_RETRY_BACKOFF_SECONDS)
    return None


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
        """Retries a transient DB failure in place before giving up — see
        _run_with_retry(). A Stripe-side failure is handled inside
        refund_payment() itself and doesn't propagate here, so this retry
        loop only ever re-runs on a genuine DB error — see
        PaymentManager.refund_payment's own docstring for why a retried
        Stripe call (if commit failed after a successful refund) is still
        safe: same idempotency_key, no double refund."""

        async def _refund(session: AsyncSession) -> None:
            # PaymentManager.refund_payment already commits internally, per
            # this project's "Manager methods that mutate always end with
            # commit()" convention — _run_with_retry's own commit() below is
            # then a harmless no-op on an already-clean session, not a
            # second real write.
            manager = PaymentManager(session=session, payments=PaymentRepository(session))
            notification_producer = await get_notification_producer()
            await manager.refund_payment(booking_id, notification_producer)

        await _run_with_retry(
            self._session_factory,
            _refund,
            retrying_event="booking_cancelled_consumer_db_write_failed_retrying",
            failed_event="booking_cancelled_consumer_db_write_failed_permanently",
            booking_id=str(booking_id),
        )
