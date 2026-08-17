from collections.abc import Iterator
from typing import TypeVar

# Postgres/asyncpg caps a single statement at 32767 bind params — anything
# issuing a multi-row INSERT or an .in_()/subquery clause against a
# potentially large list chunks through this to stay safely under that limit
# regardless of how many rows/IDs are involved. One shared constant so the
# two call sites (ticket_repository.py's INSERT, cron_hold_strategy.py's
# sweep) can't drift out of sync with each other — sized for the wider call
# site's worst case (ticket_repository.py's INSERT, 7 params/row as of the
# price_cents column) rather than each site picking its own number, since
# cron_hold_strategy.py's single-column .in_() has much more headroom than it
# needs at this size anyway.
BIND_PARAM_SAFE_BATCH_SIZE = 4000

T = TypeVar("T")


def chunked(items: list[T], size: int = BIND_PARAM_SAFE_BATCH_SIZE) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
