import uuid

from app.kafka.consumers import _error_text
from app.kafka.schemas import NotificationAction, NotificationMessage, RetryEnvelope


def test_error_text_uses_str_when_it_has_real_content():
    assert _error_text(ValueError("card declined")) == "card declined"


def test_error_text_falls_back_to_repr_for_blank_message():
    assert _error_text(Exception("")) == repr(Exception(""))


def test_error_text_falls_back_to_repr_for_whitespace_only_message():
    # Regression: `str(exc) or repr(exc)` treats "  " as truthy, letting it through only for RetryEnvelope's strip_whitespace to collapse it to "" downstream.
    exc = Exception("   ")
    text = _error_text(exc)
    assert text == repr(exc)
    assert text.strip()


def test_whitespace_only_exception_does_not_crash_retry_envelope_construction():
    # Building a RetryEnvelope from _error_text(exc) must never raise, even for a whitespace-only message — that used to kill the consumer task.
    exc = Exception("   ")
    envelope = RetryEnvelope(
        attempt=2,
        original=NotificationMessage(action=NotificationAction.BOOKING_CONFIRMED, booking_id=uuid.uuid4()),
        last_error=_error_text(exc),
    )
    assert envelope.last_error
