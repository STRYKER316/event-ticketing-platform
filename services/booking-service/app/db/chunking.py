from collections.abc import Iterator
from typing import TypeVar

# Postgres/asyncpg caps a statement at 32767 bind params; shared here so the two chunked call sites can't drift, sized for the wider one's worst case.
BIND_PARAM_SAFE_BATCH_SIZE = 4000

T = TypeVar("T")


def chunked(items: list[T], size: int = BIND_PARAM_SAFE_BATCH_SIZE) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
