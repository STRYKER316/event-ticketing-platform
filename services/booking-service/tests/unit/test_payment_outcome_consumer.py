import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.kafka.consumers import PaymentOutcomeConsumer
from app.db.models import BookingStatus

pytestmark = pytest.mark.asyncio


def _message(action: str, booking_id: uuid.UUID, ticket_id: uuid.UUID) -> bytes:
    return f'{{"action": "{action}", "booking_id": "{booking_id}", "ticket_id": "{ticket_id}"}}'.encode()


def _consumer(session_factory) -> PaymentOutcomeConsumer:
    return PaymentOutcomeConsumer(consumer=None, session_factory=session_factory, redis=AsyncMock())


async def test_succeeded_message_confirms_hold_when_transition_wins():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    bookings_repo = AsyncMock(transition_if_pending=AsyncMock(return_value=True))
    session_factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False)))
    strategy = AsyncMock()

    with (
        patch("app.kafka.consumers.BookingRepository", return_value=bookings_repo),
        patch("app.kafka.consumers.get_hold_strategy", return_value=strategy),
    ):
        await _consumer(session_factory)._handle(_message("succeeded", booking_id, ticket_id))

    bookings_repo.transition_if_pending.assert_awaited_once_with(booking_id, BookingStatus.CONFIRMED)
    strategy.confirm_hold.assert_awaited_once_with(ticket_id)
    strategy.release_hold.assert_not_awaited()


async def test_failed_message_releases_hold_when_transition_wins():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    bookings_repo = AsyncMock(transition_if_pending=AsyncMock(return_value=True))
    session_factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False)))
    strategy = AsyncMock()

    with (
        patch("app.kafka.consumers.BookingRepository", return_value=bookings_repo),
        patch("app.kafka.consumers.get_hold_strategy", return_value=strategy),
    ):
        await _consumer(session_factory)._handle(_message("failed", booking_id, ticket_id))

    bookings_repo.transition_if_pending.assert_awaited_once_with(booking_id, BookingStatus.EXPIRED)
    strategy.release_hold.assert_awaited_once_with(ticket_id)
    strategy.confirm_hold.assert_not_awaited()


async def test_redelivered_message_on_already_terminal_booking_is_a_safe_no_op():
    # The correctness contract this task exists to satisfy (§7's general
    # idempotent-consumer rule): a redelivered message that matches zero
    # rows must not touch the hold strategy at all — a later booking may
    # already legitimately hold the same ticket.
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    bookings_repo = AsyncMock(transition_if_pending=AsyncMock(return_value=False))
    session_factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False)))
    strategy = AsyncMock()

    with (
        patch("app.kafka.consumers.BookingRepository", return_value=bookings_repo),
        patch("app.kafka.consumers.get_hold_strategy", return_value=strategy),
    ):
        await _consumer(session_factory)._handle(_message("succeeded", booking_id, ticket_id))
        await _consumer(session_factory)._handle(_message("failed", booking_id, ticket_id))

    strategy.confirm_hold.assert_not_awaited()
    strategy.release_hold.assert_not_awaited()


async def test_malformed_message_does_not_raise():
    session_factory = MagicMock()
    await _consumer(session_factory)._handle(b"not json")
