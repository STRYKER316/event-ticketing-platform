import contextlib
from collections.abc import Iterator

from app.core import Settings, get_settings


@contextlib.contextmanager
def override_settings(**overrides: object) -> Iterator[Settings]:
    """Temporarily sets fields on the process-wide get_settings() singleton
    and restores their original values on exit — collapses the repeated
    save/mutate/try-finally-restore boilerplate each unit test would
    otherwise hand-roll around the same lru_cache'd Settings instance."""
    settings = get_settings()
    original = {name: getattr(settings, name) for name in overrides}
    for name, value in overrides.items():
        setattr(settings, name, value)
    try:
        yield settings
    finally:
        for name, value in original.items():
            setattr(settings, name, value)
