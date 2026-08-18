import uuid

import pytest

from app.core import get_settings
from app.kafka.schemas import NotificationAction, NotificationMessage
from app.logic.notification_manager import NotificationManager, SimulatedDeliveryFailure

pytestmark = pytest.mark.asyncio


def _message() -> NotificationMessage:
    return NotificationMessage(action=NotificationAction.BOOKING_CONFIRMED, booking_id=uuid.uuid4())


async def test_deliver_succeeds_when_simulated_failure_disabled():
    settings = get_settings()
    original = settings.simulated_failure_attempts
    settings.simulated_failure_attempts = 0
    try:
        await NotificationManager().deliver(_message(), attempt=1)
    finally:
        settings.simulated_failure_attempts = original


async def test_deliver_raises_while_attempt_is_within_simulated_failure_window():
    settings = get_settings()
    original = settings.simulated_failure_attempts
    settings.simulated_failure_attempts = 2
    try:
        with pytest.raises(SimulatedDeliveryFailure):
            await NotificationManager().deliver(_message(), attempt=1)
        with pytest.raises(SimulatedDeliveryFailure):
            await NotificationManager().deliver(_message(), attempt=2)
        # attempt 3 is past the simulated-failure window — succeeds.
        await NotificationManager().deliver(_message(), attempt=3)
    finally:
        settings.simulated_failure_attempts = original
