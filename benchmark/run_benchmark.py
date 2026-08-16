"""Concurrent-client load harness for the P8 hold-mechanism benchmark (§6).

Standalone tool, not part of the /services uv workspace -- mirrors
infra/kafka-smoke-test's pattern (own pyproject.toml, own venv).

Measures, against a running booking-service (via Traefik):
  - successful/failed booking counts under a fixed burst of concurrent
    clients racing for a small, fixed seat pool
  - hold-acquisition latency (p50/p95/p99) for successful bookings
  - time-to-release-after-abandonment (passive path): one extra booking is
    deliberately left unconfirmed, and the harness polls Postgres until the
    passive release path (cron sweep interval / Redis TTL, whichever
    HOLD_STRATEGY the server is actually running) marks it EXPIRED
  - (optional, --measure-immediate-release) time-to-release for an
    *immediately triggered* release (§17): P4/the payment.failed Kafka
    consumer don't exist yet, so this simulates that trigger directly --
    performs the same write TicketHoldStrategy.release_hold() would (a
    Postgres UPDATE for cron, a Redis DEL for redis), then polls the same
    Booking.status PENDING -> EXPIRED signal the passive measurement uses,
    for an apples-to-apples comparison of trigger latency, not just
    observation methodology. See docs/decisions-log.md §17 amendment
    (P8.T5) for why this simulates the write directly rather than adding a
    production endpoint or importing booking-service's app code.

Fixed, documented, re-runnable command (stack already up via `make up`,
run from repo root):

    cd benchmark
    set -a && . ../.env && set +a
    uv run python run_benchmark.py --label cron

`--label` only tags the output file/summary -- it does not set the
server's HOLD_STRATEGY. Run once per strategy (restart booking-service
with HOLD_STRATEGY=cron / HOLD_STRATEGY=redis between runs, see
infra/README.md) for the P8.T3/T4 comparison, same --seat-pool-size and
--clients-per-seat both times so the load profile is identical. Pass
--measure-immediate-release --hold-strategy {cron,redis} (matching
whichever HOLD_STRATEGY the server is actually running) for the P8.T5
release-latency comparison.
"""

import argparse
import asyncio
import json
import os
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx
from redis.asyncio import Redis as AsyncRedis

DEFAULT_BASE_URL = "http://localhost"
RESULTS_DIR = Path(__file__).parent / "results"

# Seed users from infra/keycloak/realm-export.json (§5). Organizer role
# needed to create/publish the benchmark event; the booker identity just
# needs to be authenticated -- POST /bookings has no role requirement.
# One shared booker token is reused across every synthetic client rather
# than minting one per client: the mechanism under test (the atomic
# per-ticket UPDATE / Redis SET NX EX) is keyed on ticket_id, not
# user_subject, so distinct identities add Keycloak token-minting
# overhead to the measured setup without changing what's being measured.
ORGANIZER_USERNAME = "carol"
ORGANIZER_PASSWORD = "changeme"
BOOKER_USERNAME = "alice"
BOOKER_PASSWORD = "changeme"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class Config:
    def __init__(self, args: argparse.Namespace):
        self.base_url = args.base_url
        self.keycloak_host = _env("KEYCLOAK_HOST", "localhost")
        self.keycloak_port = _env("KEYCLOAK_PORT", "8081")
        self.keycloak_realm = _env("KEYCLOAK_REALM", "ticketing")
        # Confidential, direct-access-grant client -- same one get-token.sh uses.
        self.keycloak_client_id = "ticketing-service"
        self.keycloak_client_secret = "changeme"

        self.postgres_host = _env("POSTGRES_HOST", "localhost")
        self.postgres_port = int(_env("POSTGRES_PORT", "55432"))
        self.booking_db_name = _env("BOOKING_DB_NAME", "booking_db")
        self.booking_db_user = _env("BOOKING_DB_USER", "booking_service")
        self.booking_db_password = _env("BOOKING_DB_PASSWORD", "changeme")

        self.redis_host = _env("REDIS_HOST", "localhost")
        self.redis_port = int(_env("REDIS_PORT", "6379"))

        self.label = args.label
        self.seat_pool_size = args.seat_pool_size
        self.clients_per_seat = args.clients_per_seat
        self.out = args.out
        self.skip_release_latency = args.skip_release_latency
        self.release_poll_interval = args.release_poll_interval
        self.release_max_wait = args.release_max_wait
        self.measure_immediate_release = args.measure_immediate_release
        self.hold_strategy = args.hold_strategy

    @property
    def token_url(self) -> str:
        return (
            f"http://{self.keycloak_host}:{self.keycloak_port}"
            f"/realms/{self.keycloak_realm}/protocol/openid-connect/token"
        )

    @property
    def booking_dsn(self) -> str:
        return (
            f"postgresql://{self.booking_db_user}:{self.booking_db_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.booking_db_name}"
        )


# --------------------------------------------------------------------------
# Keycloak
# --------------------------------------------------------------------------


async def get_token(client: httpx.AsyncClient, cfg: Config, username: str, password: str) -> str:
    response = await client.post(
        cfg.token_url,
        data={
            "grant_type": "password",
            "client_id": cfg.keycloak_client_id,
            "client_secret": cfg.keycloak_client_secret,
            "username": username,
            "password": password,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


# --------------------------------------------------------------------------
# Setup: provision a fresh, self-contained seat pool via the real API
# --------------------------------------------------------------------------


async def provision_seat_pool(client: httpx.AsyncClient, cfg: Config, organizer_token: str, total_seats: int) -> str:
    headers = {"Authorization": f"Bearer {organizer_token}"}
    run_tag = uuid.uuid4().hex[:8]

    venue_resp = await client.post(
        f"{cfg.base_url}/venues",
        headers=headers,
        json={"name": f"P8 Benchmark Venue {run_tag}", "address": "1 Benchmark Way", "capacity": total_seats},
    )
    venue_resp.raise_for_status()
    venue_id = venue_resp.json()["id"]

    now = datetime.now(timezone.utc)
    event_resp = await client.post(
        f"{cfg.base_url}/events",
        headers=headers,
        json={
            "title": f"P8 Benchmark Run {run_tag}",
            "start_time": (now + timedelta(hours=1)).isoformat(),
            "end_time": (now + timedelta(hours=3)).isoformat(),
            "venue_id": venue_id,
        },
    )
    event_resp.raise_for_status()
    event_id = event_resp.json()["id"]

    seats = [{"label": f"S{i}", "x": float(i), "y": 1.0} for i in range(1, total_seats + 1)]
    seat_map_resp = await client.put(
        f"{cfg.base_url}/events/{event_id}/seat-map",
        headers=headers,
        json={"sections": [{"name": "Benchmark", "rows": [{"name": "1", "seats": seats}]}]},
    )
    seat_map_resp.raise_for_status()

    publish_resp = await client.post(f"{cfg.base_url}/events/{event_id}/publish", headers=headers)
    publish_resp.raise_for_status()

    return event_id


async def wait_for_tickets(pool: asyncpg.Pool, event_id: str, expected_count: int, timeout: float = 30.0) -> list[str]:
    """Booking-service provisions tickets asynchronously off the Kafka
    publish event -- no synchronous API confirms it, so poll booking_db
    directly (read-only; database-per-service is about services not
    querying each other's tables, not about an external test harness
    observing state, same category as the existing testcontainers suites)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await pool.fetch(
            "SELECT id::text AS id FROM tickets WHERE event_id = $1 AND status = 'AVAILABLE' ORDER BY seat_label",
            uuid.UUID(event_id),
        )
        if len(rows) >= expected_count:
            return [row["id"] for row in rows]
        await asyncio.sleep(0.5)
    raise TimeoutError(f"tickets not provisioned within {timeout}s for event {event_id}")


# --------------------------------------------------------------------------
# Contention burst
# --------------------------------------------------------------------------


async def attempt_booking(client: httpx.AsyncClient, cfg: Config, token: str, ticket_id: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    start = time.perf_counter()
    try:
        response = await client.post(f"{cfg.base_url}/bookings", headers=headers, json={"ticket_id": ticket_id})
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "latency": time.perf_counter() - start, "error": str(exc)}

    elapsed = time.perf_counter() - start
    if response.status_code == 201:
        return {"ok": True, "status_code": 201, "latency": elapsed, "booking_id": response.json()["id"]}
    return {"ok": False, "status_code": response.status_code, "latency": elapsed}


async def run_contention_burst(
    client: httpx.AsyncClient, cfg: Config, token: str, ticket_ids: list[str], clients_per_seat: int
) -> list[dict]:
    # Burst ramp: every client fires at once via asyncio.gather -- the same
    # concurrent-client pattern already proven in P3.T7's concurrency suite
    # (tests/integration/test_concurrency_suite.py), generalized from one
    # contended seat to `seat_pool_size` seats contended in parallel.
    tasks = [
        attempt_booking(client, cfg, token, ticket_id) for ticket_id in ticket_ids for _ in range(clients_per_seat)
    ]
    return await asyncio.gather(*tasks)


# --------------------------------------------------------------------------
# Release-latency (passive path) and immediate-trigger simulation (§17)
# --------------------------------------------------------------------------


async def _poll_until_expired(
    pool: asyncpg.Pool, booking_id: uuid.UUID, poll_interval: float, max_wait: float
) -> dict:
    # Booking.status PENDING -> EXPIRED is the one signal both strategies
    # actually produce on release: the cron strategy also flips
    # Ticket.status HELD -> AVAILABLE, but the Redis strategy never writes
    # Ticket.status at all (§6) -- polling the Booking row is the only
    # observation that works identically for both, and for the immediate
    # path below, the only one directly comparable to this passive one.
    start = time.monotonic()
    deadline = start + max_wait
    while time.monotonic() < deadline:
        row = await pool.fetchrow("SELECT status FROM bookings WHERE id = $1", booking_id)
        if row is not None and row["status"] == "EXPIRED":
            return {"measured_seconds": time.monotonic() - start, "timed_out": False, "skipped": False}
        await asyncio.sleep(poll_interval)
    return {"measured_seconds": None, "timed_out": True, "skipped": False}


async def measure_release_latency(
    client: httpx.AsyncClient, cfg: Config, pool: asyncpg.Pool, token: str, ticket_id: str
) -> dict:
    response = await client.post(
        f"{cfg.base_url}/bookings", headers={"Authorization": f"Bearer {token}"}, json={"ticket_id": ticket_id}
    )
    response.raise_for_status()
    booking_id = uuid.UUID(response.json()["id"])
    return await _poll_until_expired(pool, booking_id, cfg.release_poll_interval, cfg.release_max_wait)


async def simulate_immediate_release(
    client: httpx.AsyncClient,
    cfg: Config,
    pool: asyncpg.Pool,
    redis_client: AsyncRedis,
    token: str,
    ticket_id: str,
    hold_strategy: str,
) -> dict:
    """Simulates the §17 payment.failed -> immediate-release trigger, which
    has no real caller yet (P4/the Kafka consumer for it don't exist -- see
    docs/decisions-log.md §17 P8.T5 amendment). Performs the same write
    TicketHoldStrategy.release_hold() would (a Postgres UPDATE for cron, a
    Redis DEL for redis -- mirrored directly from
    app/logic/helpers/{cron,redis}_hold_strategy.py rather than importing
    booking-service's app code into this standalone tool's separate venv),
    plus the Booking-row update a real consumer would make alongside it,
    then polls the identical Booking.status signal the passive measurement
    uses -- same observation methodology, different trigger."""
    response = await client.post(
        f"{cfg.base_url}/bookings", headers={"Authorization": f"Bearer {token}"}, json={"ticket_id": ticket_id}
    )
    response.raise_for_status()
    booking_id = uuid.UUID(response.json()["id"])
    ticket_uuid = uuid.UUID(ticket_id)

    trigger_start = time.monotonic()
    async with pool.acquire() as conn, conn.transaction():
        if hold_strategy == "cron":
            await conn.execute(
                "UPDATE tickets SET status = 'AVAILABLE', hold_expires_at = NULL WHERE id = $1 AND status = 'HELD'",
                ticket_uuid,
            )
        else:
            await redis_client.delete(f"ticket:hold:{ticket_uuid}")
        await conn.execute("UPDATE bookings SET status = 'EXPIRED' WHERE id = $1 AND status = 'PENDING'", booking_id)
    trigger_elapsed = time.monotonic() - trigger_start

    observed = await _poll_until_expired(pool, booking_id, poll_interval=0.05, max_wait=5.0)
    observed["trigger_write_seconds"] = trigger_elapsed
    return observed


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def summarize_latencies(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0}
    return {
        "count": len(samples),
        "mean": statistics.fmean(samples),
        "min": min(samples),
        "max": max(samples),
        "p50": _percentile(samples, 0.50),
        "p95": _percentile(samples, 0.95),
        "p99": _percentile(samples, 0.99),
    }


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


async def main_async(cfg: Config) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        organizer_token = await get_token(client, cfg, ORGANIZER_USERNAME, ORGANIZER_PASSWORD)
        booker_token = await get_token(client, cfg, BOOKER_USERNAME, BOOKER_PASSWORD)

        # One extra seat for the passive release-latency measurement, plus
        # one more if the immediate-trigger simulation is also requested --
        # both reserved outside the contended pool so neither competes with
        # the burst.
        extra_seats = 1 + (1 if cfg.measure_immediate_release else 0)
        total_seats = cfg.seat_pool_size + extra_seats
        event_id = await provision_seat_pool(client, cfg, organizer_token, total_seats)

        pool = await asyncpg.create_pool(cfg.booking_dsn, min_size=1, max_size=5)
        redis_client = AsyncRedis(host=cfg.redis_host, port=cfg.redis_port) if cfg.measure_immediate_release else None
        try:
            ticket_ids = await wait_for_tickets(pool, event_id, total_seats)
            contended_tickets = ticket_ids[: cfg.seat_pool_size]
            release_ticket = ticket_ids[cfg.seat_pool_size]

            burst_started = datetime.now(timezone.utc).isoformat()
            results = await run_contention_burst(client, cfg, booker_token, contended_tickets, cfg.clients_per_seat)
            burst_finished = datetime.now(timezone.utc).isoformat()

            successes = [r for r in results if r["ok"]]
            failures = [r for r in results if not r["ok"]]

            if cfg.skip_release_latency:
                release_result = {"skipped": True, "measured_seconds": None, "timed_out": None}
            else:
                release_result = await measure_release_latency(client, cfg, pool, booker_token, release_ticket)

            immediate_release_result = None
            if cfg.measure_immediate_release:
                immediate_ticket = ticket_ids[cfg.seat_pool_size + 1]
                immediate_release_result = await simulate_immediate_release(
                    client, cfg, pool, redis_client, booker_token, immediate_ticket, cfg.hold_strategy
                )

            output = {
                "label": cfg.label,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "event_id": event_id,
                "load_profile": {
                    "seat_pool_size": cfg.seat_pool_size,
                    "clients_per_seat": cfg.clients_per_seat,
                    "total_clients": cfg.seat_pool_size * cfg.clients_per_seat,
                    "ramp": "burst (asyncio.gather, all clients fired at once)",
                },
                "contention_burst": {
                    "started_at": burst_started,
                    "finished_at": burst_finished,
                    "successful_count": len(successes),
                    "failed_count": len(failures),
                    "expected_successful_count": cfg.seat_pool_size,
                    "hold_acquisition_latency_seconds": summarize_latencies([r["latency"] for r in successes]),
                    "failed_latency_seconds": summarize_latencies([r["latency"] for r in failures]),
                },
                "release_latency": {
                    "poll_interval_seconds": cfg.release_poll_interval,
                    "max_wait_seconds": cfg.release_max_wait,
                    **release_result,
                },
                "raw_samples": results,
            }
            if immediate_release_result is not None:
                output["immediate_release_latency"] = {"hold_strategy": cfg.hold_strategy, **immediate_release_result}
            return output
        finally:
            await pool.close()
            if redis_client is not None:
                await redis_client.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--label",
        required=True,
        help="Tags this run's output (e.g. 'cron' or 'redis'). Must match what HOLD_STRATEGY the "
        "booking-service container is actually running -- this script does not set it.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Traefik entrypoint (default: %(default)s)")
    parser.add_argument("--seat-pool-size", type=int, default=30)
    parser.add_argument("--clients-per-seat", type=int, default=10)
    parser.add_argument(
        "--skip-release-latency",
        action="store_true",
        help="Skip the passive release-path measurement (fast iteration only -- P8.T3/T4 archived runs must NOT use this flag).",
    )
    parser.add_argument("--release-poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--release-max-wait",
        type=float,
        default=700.0,
        help="Must exceed the running booking-service's HOLD_TTL_SECONDS + HOLD_SWEEP_INTERVAL_SECONDS "
        "or the release measurement times out (defaults: 600 + 30 = 630s).",
    )
    parser.add_argument("--out", default=None, help="Output path; defaults to results/<label>-<timestamp>.json")
    parser.add_argument(
        "--measure-immediate-release",
        action="store_true",
        help="Also simulate the §17 immediate-release trigger (P8.T5) -- requires --hold-strategy.",
    )
    parser.add_argument(
        "--hold-strategy",
        choices=["cron", "redis"],
        default=None,
        help="Which mechanism to simulate for --measure-immediate-release. Must match what "
        "HOLD_STRATEGY the booking-service container is actually running.",
    )
    args = parser.parse_args()
    if args.measure_immediate_release and args.hold_strategy is None:
        parser.error("--measure-immediate-release requires --hold-strategy")
    return args


def main() -> None:
    args = parse_args()
    cfg = Config(args)
    result = asyncio.run(main_async(cfg))

    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(cfg.out) if cfg.out else RESULTS_DIR / f"{cfg.label}-{timestamp}.json"
    out_path.write_text(json.dumps(result, indent=2))

    burst = result["contention_burst"]
    release = result["release_latency"]
    print(f"label={cfg.label}")
    print(
        f"successful={burst['successful_count']} failed={burst['failed_count']} "
        f"(expected successful={burst['expected_successful_count']})"
    )
    lat = burst["hold_acquisition_latency_seconds"]
    if lat.get("count"):
        print(f"hold-acquisition latency (s): p50={lat['p50']:.4f} p95={lat['p95']:.4f} p99={lat['p99']:.4f}")
    if release["skipped"]:
        print("release-latency: skipped (--skip-release-latency)")
    elif release["timed_out"]:
        print(f"release-latency: TIMED OUT after {release['max_wait_seconds']}s")
    else:
        print(f"release-latency (passive): {release['measured_seconds']:.2f}s")
    immediate = result.get("immediate_release_latency")
    if immediate is not None:
        if immediate["timed_out"]:
            print("release-latency (immediate): TIMED OUT")
        else:
            print(
                f"release-latency (immediate, {immediate['hold_strategy']}): "
                f"trigger-write={immediate['trigger_write_seconds']:.4f}s, "
                f"observed={immediate['measured_seconds']:.4f}s"
            )
    print(f"raw output archived: {out_path}")


if __name__ == "__main__":
    main()
