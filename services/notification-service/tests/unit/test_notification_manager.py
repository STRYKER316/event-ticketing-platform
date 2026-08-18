import uuid

import pytest

from app.kafka.schemas import NotificationAction, NotificationMessage
from app.logic.notification_manager import NotificationManager, SimulatedDeliveryFailure

from .conftest import override_settings

pytestmark = pytest.mark.asyncio


def _message() -> NotificationMessage:
    return NotificationMessage(action=NotificationAction.BOOKING_CONFIRMED, booking_id=uuid.uuid4())


async def test_deliver_succeeds_when_simulated_failure_disabled():
    with override_settings(simulated_failure_attempts=0):
        await NotificationManager().deliver(_message(), attempt=1)


async def test_deliver_raises_while_attempt_is_within_simulated_failure_window():
    with override_settings(simulated_failure_attempts=2):
        with pytest.raises(SimulatedDeliveryFailure):
            await NotificationManager().deliver(_message(), attempt=1)
        with pytest.raises(SimulatedDeliveryFailure):
            await NotificationManager().deliver(_message(), attempt=2)
        # attempt 3 is past the simulated-failure window — succeeds.
        await NotificationManager().deliver(_message(), attempt=3)
