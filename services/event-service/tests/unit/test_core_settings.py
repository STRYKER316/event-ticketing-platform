import pytest
from pydantic import ValidationError

from app.core import Settings


def test_settings_log_level_defaults_to_info():
    assert Settings().log_level == "INFO"


def test_settings_log_level_normalizes_case():
    # Preserves the pre-existing case-insensitive env var behavior now that this is a real Literal rather than a bare str.
    assert Settings(log_level="debug").log_level == "DEBUG"


def test_settings_log_level_rejects_an_unrecognized_value():
    # A typo'd LOG_LEVEL env value must fail at startup, not silently no-op to INFO the way the old bare-str field did.
    with pytest.raises(ValidationError):
        Settings(log_level="verbose")
