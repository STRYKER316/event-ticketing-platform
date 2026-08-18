from app.core import get_settings
from app.logic.helpers.backoff import compute_backoff_seconds


def test_backoff_grows_with_attempt():
    settings = get_settings()
    original_base, original_cap = settings.retry_base_backoff_seconds, settings.retry_backoff_cap_seconds
    settings.retry_base_backoff_seconds = 2.0
    settings.retry_backoff_cap_seconds = 1000.0
    try:
        assert compute_backoff_seconds(1) == 2.0
        assert compute_backoff_seconds(2) == 4.0
        assert compute_backoff_seconds(3) == 8.0
    finally:
        settings.retry_base_backoff_seconds, settings.retry_backoff_cap_seconds = original_base, original_cap


def test_backoff_is_capped():
    settings = get_settings()
    original_base, original_cap = settings.retry_base_backoff_seconds, settings.retry_backoff_cap_seconds
    settings.retry_base_backoff_seconds = 2.0
    settings.retry_backoff_cap_seconds = 10.0
    try:
        assert compute_backoff_seconds(10) == 10.0
    finally:
        settings.retry_base_backoff_seconds, settings.retry_backoff_cap_seconds = original_base, original_cap
