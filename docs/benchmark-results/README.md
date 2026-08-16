# P8 benchmark results (measured, archived)

Raw output from `benchmark/run_benchmark.py` (§6, §17) — real execution
against the live compose stack, not estimated or placeholder numbers (see
CLAUDE.md's Integrity rule). Source for the Feature Development Process
report chapter's analysis (P8.T6).

**Revision note (2026-08-16):** the first version of this data (a single
run per strategy) was superseded after a dedicated adversarial
`/code-review` pass and an independent `/pre-pr` review both caught a real
measurement bug: the harness's `httpx.AsyncClient` used its default
100-connection cap, so ~200 of the 300-request burst queued client-side
*before* the request even reached the server — that queueing time was
being counted as "hold-acquisition latency." Fixed (explicit
`httpx.Limits` sized to the load profile) and re-run three times per
strategy, both to get past the connection-cap artifact and because a
single run cannot support a "clean win" claim on its own. The corrected,
n=3 numbers below are materially different from — and more mixed than —
the original single-run result. See `docs/build-log.md`'s P8 CHECKPOINT
entry for the full story.

## Fixed load profile (identical for both runs, x3 each)

- **Seat pool:** 30 tickets, provisioned fresh per run via the real
  `event-service`/`booking-service` APIs.
- **Clients:** 10 concurrent clients per seat (300 total requests).
- **Ramp:** burst — every request fired at once via `asyncio.gather`.
- **HTTP client:** connection pool sized to the load profile
  (`max_connections = seat_pool_size * clients_per_seat + 20`) so
  client-side queueing never contaminates measured latency.
- **Hold TTL / sweep interval:** `HOLD_TTL_SECONDS=10`,
  `HOLD_SWEEP_INTERVAL_SECONDS=5` (not the compose defaults of 600/30) —
  set explicitly for benchmark reproducibility. This only bounds how long
  the *passive* release path takes to fire; it does not change the
  contention-burst mechanism.

Command (from `benchmark/`, after `set -a && . ../.env && set +a`), run
three times per strategy:

```sh
uv run python run_benchmark.py --label cron  --seat-pool-size 30 --clients-per-seat 10 \
  --release-poll-interval 1 --release-max-wait 60 --measure-immediate-release --hold-strategy cron

uv run python run_benchmark.py --label redis --seat-pool-size 30 --clients-per-seat 10 \
  --release-poll-interval 1 --release-max-wait 60 --measure-immediate-release --hold-strategy redis
```

`booking-service` was restarted between strategies with the strategy under
test (`docker compose run -e HOLD_STRATEGY=... -e HOLD_TTL_SECONDS=10 -e
HOLD_SWEEP_INTERVAL_SECONDS=5 --use-aliases booking-service`, see
`benchmark/README.md`).

## Files

| File | What it is |
|---|---|
| `cron-run-{1,2,3}.json` | Full harness output, `HOLD_STRATEGY=cron`, three independent runs — contention-burst counts, hold-acquisition latency percentiles, passive release latency, immediate-release simulation, and all 300 raw per-request samples each. |
| `redis-run-{1,2,3}.json` | Same, `HOLD_STRATEGY=redis`. |
| `cron-grafana-export.json` / `redis-grafana-export.json` | Prometheus query results for all four `booking-service` dashboard panels, captured via Grafana's datasource-proxy API (browser screenshot unavailable — the `claude-in-chrome` extension wasn't connected this session). |

## Aggregate results (n=3 per strategy)

| Metric | cron (mean, range) | redis (mean, range) |
|---|---|---|
| Successful bookings (of 30) | 30/30, 30/30, 30/30 | 30/30, 30/30, 30/30 |
| Hold-acquisition latency p50 | 0.431s (0.267–0.524) | 0.446s (0.279–0.702) |
| Hold-acquisition latency p95 | 1.052s (0.731–1.233) | 1.257s (1.074–1.559) |
| Hold-acquisition latency p99 | 1.144s (0.830–1.333) | 1.417s (1.252–1.660) |
| Passive release latency | 12.07s (11.07–13.08) | 12.73s (11.05–14.07) |
| Immediate-release trigger-write | 4.28ms (3.58–4.89) | 6.69ms (4.87–8.42) |

**Honest read:** at n=3, hold-acquisition p50 and passive release latency
are statistically indistinguishable between strategies — the ranges
overlap substantially, and three samples is not enough to call a
difference that small significant. p95/p99 show redis trending slightly
higher, and immediate-release trigger-write is the one metric where redis
was slower in all three runs (consistent with the extra network hop to a
separate Redis container), though the absolute gap (a few milliseconds)
is trivial next to the ~12-second passive-release numbers either metric
is being compared against. See the Feature Development Process report
chapter for the full discussion — this is a genuinely mixed result, not
a clean win for either strategy, and is reported as such.

Both strategies correctly allowed exactly one winner per contended seat
(30 successes, 270 failures — the losers of each 10-way race, all real
`409`s per `failed_status_code_breakdown`, not masked errors), matching
the correctness guarantee already proven in P3.T7's concurrency suite;
this benchmark measures throughput/latency under that guarantee, not
whether it holds (already established).
