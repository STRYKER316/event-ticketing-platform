# benchmark

Standalone concurrent-client load harness for the P8 Hold-Mechanism Benchmark
(decisions-log §6) — not part of the `/services` uv workspace, mirrors
`infra/kafka-smoke-test`'s pattern (own `pyproject.toml`, own `.venv`).

Resolved 2026-08-16: Python asyncio harness over k6 (open question in
`docs/phases/phase-8-kickoff.md`) — reuses the exact `asyncio.gather`
concurrent-client pattern already proven in P3.T7's concurrency suite
(`services/booking-service/tests/integration/test_concurrency_suite.py`),
no new (Go-based) dependency for a project that's Python end-to-end
everywhere else.

## What it measures

Against a running `booking-service` (via Traefik), in one invocation:

- **Successful/failed booking counts** under a fixed burst of concurrent
  clients racing for a small, fixed seat pool.
- **Hold-acquisition latency** (p50/p95/p99) for successful bookings.
- **Time-to-release-after-abandonment**: one extra booking is deliberately
  left unconfirmed, and the harness polls `booking_db` directly until the
  passive release path (cron sweep interval / Redis TTL, whichever
  `HOLD_STRATEGY` the server is actually running) marks it `EXPIRED`.

## Load profile

- **Seat pool**: `--seat-pool-size` tickets (default 30), provisioned fresh
  each run via the real `event-service`/`booking-service` APIs (venue ->
  event -> seat map -> publish -> wait for Kafka provisioning) — no
  dependency on ambient seed/test data, so every run is self-contained.
- **Clients**: `--clients-per-seat` (default 10) concurrent clients race
  each pooled seat — `seat_pool_size * clients_per_seat` total requests.
- **Ramp**: burst — every request fires at once via `asyncio.gather`, no
  gradual ramp-up.
- One extra ticket beyond the pool is reserved for the release-latency
  measurement so it never competes with the burst.

Identity: one Keycloak-authenticated organizer token (`carol`) provisions
the event; one shared booker token (`alice`) is reused across every
synthetic client. The mechanism under test (the atomic per-ticket
`UPDATE` / Redis `SET NX EX`) is keyed on `ticket_id`, not `user_subject`,
so distinct identities would only add Keycloak token-minting overhead to
the measured setup without changing what's being measured.

## Running it

Stack must already be up (`make up` from repo root) and migrated/seeded is
not required — the harness provisions its own data.

```sh
cd benchmark
uv sync                        # first run only
set -a && . ../.env && set +a
uv run python run_benchmark.py --label cron
```

`--label` only tags the output file and summary — it does **not** set the
server's `HOLD_STRATEGY`. For the P8.T3/T4 cron-vs-Redis comparison, restart
`booking-service` with the strategy under test between runs (see
`infra/README.md`), using the identical `--seat-pool-size`/
`--clients-per-seat` both times so the load profile is comparable:

```sh
# cron run
docker compose --env-file ../.env up -d booking-service   # HOLD_STRATEGY=cron, the compose default
uv run python run_benchmark.py --label cron

# redis run
docker compose --env-file ../.env run -d --rm --name booking-service-redis \
  -e HOLD_STRATEGY=redis --use-aliases booking-service
uv run python run_benchmark.py --label redis
docker rm -f booking-service-redis
docker compose --env-file ../.env up -d booking-service   # restore cron default
```

Output: `results/<label>-<timestamp>.json` (gitignored — local scratch
runs). The runs that actually feed the report get copied into `/docs`
deliberately as part of P8.T3/T4, not every ad hoc invocation of this
script.

## Options

| Flag | Default | Notes |
|---|---|---|
| `--label` | required | Tags output only |
| `--base-url` | `http://localhost` | Traefik entrypoint |
| `--seat-pool-size` | 30 | Contended seats |
| `--clients-per-seat` | 10 | Concurrent racers per seat |
| `--skip-release-latency` | off | Fast iteration only — **P8.T3/T4 archived runs must not use this** |
| `--release-poll-interval` | 2.0s | Poll cadence while waiting for release |
| `--release-max-wait` | 700.0s | Must exceed the server's `HOLD_TTL_SECONDS + HOLD_SWEEP_INTERVAL_SECONDS` (defaults: 600 + 30 = 630s) or the measurement times out |
| `--out` | `results/<label>-<timestamp>.json` | Override output path |

For fast iteration, shrink the server's hold TTL/sweep interval rather than
waiting out the 600s default — this is what the harness's own live
verification used (`HOLD_TTL_SECONDS=5 HOLD_SWEEP_INTERVAL_SECONDS=5` via
`docker compose run -e ... --use-aliases booking-service`, same trick used
during the Phase 3 checkpoint's live re-verification, see `docs/build-log.md`).
