from app.logic.helpers.backoff import compute_backoff_seconds

from .conftest import override_settings


def test_backoff_grows_with_attempt():
    with override_settings(retry_base_backoff_seconds=2.0, retry_backoff_cap_seconds=1000.0):
        assert compute_backoff_seconds(1) == 2.0
        assert compute_backoff_seconds(2) == 4.0
        assert compute_backoff_seconds(3) == 8.0


def test_backoff_is_capped():
    with override_settings(retry_base_backoff_seconds=2.0, retry_backoff_cap_seconds=10.0):
        assert compute_backoff_seconds(10) == 10.0


def test_backoff_does_not_overflow_on_a_huge_attempt():
    # RetryEnvelope.attempt is DTO-bounded (le=1000), but this function must
    # stay safe on its own regardless — a value large enough to overflow
    # base**attempt before min() ever clamps it must still return the cap, not raise.
    with override_settings(retry_base_backoff_seconds=2.0, retry_backoff_cap_seconds=30.0):
        assert compute_backoff_seconds(10**9) == 30.0
