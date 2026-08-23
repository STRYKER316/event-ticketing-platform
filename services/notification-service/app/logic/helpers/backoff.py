from app.core import get_settings


def compute_backoff_seconds(attempt: int) -> float:
    """§17 amendment #2's formula — increasing backoff per retry attempt,
    capped so a misconfigured base/attempt combination can't block a
    consumer indefinitely. A pure function, unit-testable without a
    running consumer.

    base**attempt is computed before the cap is applied, so a large enough
    attempt overflows float range before min() ever gets to clamp it — caught
    here rather than trusted to RetryEnvelope's own bound, since a forged or
    corrupted envelope on the retry topic is untrusted input by the time it
    reaches this function."""
    settings = get_settings()
    try:
        uncapped = settings.retry_base_backoff_seconds**attempt
    except OverflowError:
        return settings.retry_backoff_cap_seconds
    return min(uncapped, settings.retry_backoff_cap_seconds)
