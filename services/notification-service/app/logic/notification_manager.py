import structlog

from app.core import get_settings
from app.kafka.schemas import NotificationMessage

logger = structlog.get_logger()


class SimulatedDeliveryFailure(Exception):
    """Raised only by the demo/test instrument (Settings.simulated_failure_attempts,
    decisions-log §17 amendment #3) — never a real delivery failure, since
    this service has no external dependency capable of one (§19 — log/console
    output only)."""


class NotificationManager:
    async def deliver(self, message: NotificationMessage, attempt: int) -> None:
        settings = get_settings()
        if settings.simulated_failure_attempts >= attempt:
            raise SimulatedDeliveryFailure(
                f"simulated failure — attempt {attempt} <= simulated_failure_attempts "
                f"({settings.simulated_failure_attempts}), a deliberate demo/test instrument, not a real error"
            )
        # This *is* the delivery (§19 — no real email provider, log/console output only).
        logger.info(
            "notification_delivered",
            action=message.action.value,
            booking_id=str(message.booking_id),
            attempt=attempt,
            reason=message.reason,
        )
