# P8 benchmark results (measured, archived)

Raw output from `benchmark/run_benchmark.py` (§6, §17) — real execution
against the live compose stack, not estimated or placeholder numbers (see
CLAUDE.md's Integrity rule). Source for the Feature Development Process
report chapter's analysis (P8.T6).

## Fixed load profile (identical for both runs)

- **Seat pool:** 30 tickets, provisioned fresh per run via the real
  `event-service`/`booking-service` APIs.
- **Clients:** 10 concurrent clients per seat (300 total requests).
- **Ramp:** burst — every request fired at once via `asyncio.gather`.
- **Hold TTL / sweep interval:** `HOLD_TTL_SECONDS=10`,
  `HOLD_SWEEP_INTERVAL_SECONDS=5` (not the compose defaults of 600/30) —
  set explicitly for benchmark reproducibility. This only bounds how long
  the *passive* release path takes to fire; it does not change the
  contention-burst mechanism (hold-acquisition latency/throughput), which
  is independent of TTL. Documented here rather than left implicit, per
  the Integrity rule.

Command (from `benchmark/`, after `set -a && . ../.env && set +a`):

```sh
uv run python run_benchmark.py --label cron  --seat-pool-size 30 --clients-per-seat 10 \
  --release-poll-interval 1 --release-max-wait 60 --measure-immediate-release --hold-strategy cron

uv run python run_benchmark.py --label redis --seat-pool-size 30 --clients-per-seat 10 \
  --release-poll-interval 1 --release-max-wait 60 --measure-immediate-release --hold-strategy redis
```

`booking-service` was restarted between runs with the strategy under test
(`docker compose run -e HOLD_STRATEGY=... -e HOLD_TTL_SECONDS=10 -e
HOLD_SWEEP_INTERVAL_SECONDS=5 --use-aliases booking-service`, see
`benchmark/README.md`).

## Files

| File | What it is |
|---|---|
| `cron-run.json` | Full harness output, `HOLD_STRATEGY=cron` — contention-burst counts, hold-acquisition latency percentiles, passive release latency, immediate-release simulation, and all 300 raw per-request samples. |
| `redis-run.json` | Same, `HOLD_STRATEGY=redis`. |
| `cron-grafana-export.json` | Prometheus query results for all four `booking-service` dashboard panels, captured via Grafana's datasource-proxy API shortly after the cron run (browser screenshot unavailable in this session — see note below). |
| `redis-grafana-export.json` | Same, captured after the redis run. |

**Grafana screenshot vs. export:** the kickoff doc asks for a "screenshot/
export." A live screenshot was attempted via `claude-in-chrome` browser
automation but the extension wasn't connected in this session; the
datasource-proxy JSON export was used instead — it's the same underlying
Prometheus data the dashboard panels render, just captured via API rather
than a rendered image. `infra/grafana/provisioning/dashboards/json/
booking-service.json` defines the four panels these exports correspond to;
anyone with the stack running (`make bench-up`) can open
`http://localhost:$GRAFANA_PORT/d/booking-service` and see them rendered
live.

## Headline numbers (see P8.T6 analysis in the report for full discussion)

| Metric | cron | redis |
|---|---|---|
| Successful bookings (of 30 contended seats) | 30/30 | 30/30 |
| Hold-acquisition latency p50 | 0.59s | 0.82s |
| Hold-acquisition latency p95 | 0.83s | 1.02s |
| Passive release latency (abandonment → observed EXPIRED) | 11.06s | 12.05s |
| Immediate-release trigger-write time | 5.1ms | 5.9ms |
| Immediate-release observed time | <1ms | <1ms |

Both strategies correctly allowed exactly one winner per contended seat
(30 successes, 270 failures — the losers of each 10-way race), matching
the correctness guarantee already proven in P3.T7's concurrency suite; this
benchmark measures throughput/latency under that guarantee, not whether it
holds (already established).
