from app.core import get_settings


def compute_backoff_seconds(attempt: int) -> float:
    """§17 amendment #2's formula — increasing backoff per retry attempt,
    capped so a misconfigured base/attempt combination can't block a
    consumer indefinitely. A pure function, unit-testable without a
    running consumer."""
    settings = get_settings()
    return min(settings.retry_base_backoff_seconds**attempt, settings.retry_backoff_cap_seconds)
