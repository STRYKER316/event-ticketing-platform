# Feature Development Process

*Status: Measured (P8, 2026-08-16). This chapter's centerpiece — the
cron-vs-Redis hold-mechanism comparison — is the report's only chapter
built on real load-test execution rather than unit/integration test
results alone. Every number below comes from `docs/benchmark-results/`,
archived raw output from `benchmark/run_benchmark.py` run against the live
compose stack. Per CLAUDE.md's Integrity rule, nothing here is estimated
or placeholder.*

## The feature: dual seat-hold strategy (§6)

The single hardest correctness problem in a reserved-seating ticketing
platform is preventing two concurrent requests from both successfully
booking the same seat, while still releasing an abandoned hold back into
inventory within a bounded time. Phase 3 built two independently
correct implementations of this behind one `TicketHoldStrategy` interface
(`services/booking-service/app/logic/helpers/`), selectable at runtime via
`HOLD_STRATEGY`:

- **`CronHoldStrategy`** — hold state *is* `Ticket.status`/
  `Ticket.hold_expires_at` in Postgres. Acquisition is one atomic
  conditional `UPDATE tickets SET status = 'HELD' ... WHERE status =
  'AVAILABLE'`; two concurrent transactions serialize on Postgres's row
  lock, and the loser's `WHERE` clause re-evaluates false once the winner
  commits. Abandoned holds are cleared by a periodic APScheduler sweep
  (`release_expired()`) that finds every `Ticket` past its
  `hold_expires_at` and releases it.
- **`RedisHoldStrategy`** — hold state lives *only* in Redis (`SET NX EX`);
  `Ticket.status` is never written at all. The Redis key expires on its
  own TTL with no sweep needed for that half of the state, but the
  `Booking` row `BookingManager` creates alongside the hold (§21) still
  needs its own age-based sweep (`BookingRepository.expire_stale_pending()`)
  — found during Phase 3's dedicated review, see the Class Diagrams and
  Testing Strategy chapters for the full story of that gap and its fix.

Correctness of both — "exactly one winner under N-way concurrent
contention for one seat" — was already proven in Phase 3 (P3.T7,
`test_concurrency_suite.py`, 25 concurrent clients, real Postgres/Redis via
`testcontainers`) and re-confirmed by this benchmark's own contention burst
(below). Phase 8's job is not to re-prove correctness; it's to measure
which mechanism performs better, and by how much, under real concurrent
load — this is a genuine engineering trade-off decision, not a foregone
conclusion, and decisions-log §6 deliberately left it open until it could
be measured rather than guessed.

## Methodology

**Harness:** `benchmark/run_benchmark.py`, a standalone Python `asyncio`
script (own `uv`-managed environment, not part of the `/services`
workspace). Resolved Python over k6 with the user on 2026-08-16 — it
reuses the exact `asyncio.gather` concurrent-client pattern already proven
correct in P3.T7's suite, at the cost of hand-rolling percentile reporting
that k6 would have provided out of the box. See
`docs/report/technologies-used.md` for the full trade-off writeup.

**Load profile (identical for both runs):** a fresh, self-contained
30-seat pool provisioned via the real `event-service`/`booking-service`
APIs each run (no dependency on ambient seed data); 10 concurrent clients
racing each pooled seat (300 total requests); burst ramp — every request
fired at once via `asyncio.gather`, no gradual ramp-up. One shared
Keycloak-authenticated identity is reused across every synthetic client,
since the mechanism under test is keyed on `ticket_id`, not `user_subject`
— distinct identities would add Keycloak token-minting overhead to the
measured setup without changing what's being measured.

**Reproducibility parameters:** `HOLD_TTL_SECONDS=10`,
`HOLD_SWEEP_INTERVAL_SECONDS=5` (not the compose defaults of 600s/30s) —
set explicitly so a full run, including the passive-release measurement,
completes in well under a minute rather than 10+ minutes. This only bounds
how long the *passive* release path takes to become observable; it does
not change the contention-burst mechanism (hold-acquisition latency,
throughput), which has no dependency on TTL at all.

**Three metrics measured, per run:**
1. Successful/failed booking counts under the contention burst.
2. Hold-acquisition latency (p50/p95/p99) for successful bookings.
3. Release latency, two ways: the *passive* path (an abandoned hold left
   to expire via cron sweep interval / Redis TTL, polled for), and the
   *immediate* path (§17's `payment.failed` → immediate-release trigger,
   simulated directly — see the decisions-log §17 amendment for why: P4,
   the real trigger's producer, doesn't exist yet at this point in the
   locked report-first build order, and deferring the whole metric until
   P4 would contradict the reason P8 runs before P4 in the first place).

Full command, raw archived output, and the exact restart procedure between
strategies: `docs/benchmark-results/README.md`.

## Results

### Contention burst — hold acquisition

| Metric | cron | redis |
|---|---|---|
| Successful bookings (of 30 contended seats) | 30/30 | 30/30 |
| Failed bookings (409, lost the race) | 270/270 | 270/270 |
| Hold-acquisition latency, mean | 0.551s | 0.741s |
| Hold-acquisition latency, p50 | 0.593s | 0.824s |
| Hold-acquisition latency, p95 | 0.835s | 1.021s |
| Hold-acquisition latency, p99 | 0.848s | 1.026s |

Both strategies allocated the pool exactly correctly — 30 winners, one per
seat, 270 losers — under identical 10-way-per-seat contention. This
matches, rather than merely repeats, P3.T7's correctness proof: that test
established *that* exactly one client wins; this run establishes *how
fast* winners and losers alike are told the outcome, at a scale (300
concurrent requests) an order of magnitude past P3.T7's 25.

**cron wins on acquisition latency, consistently, across every
percentile.** This is a real, measured difference, not noise — p50 is
~39% higher under Redis, p95 ~22% higher. The mechanism explains it
directly rather than leaving it a mystery: `CronHoldStrategy.acquire_hold`
does its conditional `UPDATE` and `BookingManager`'s `Booking`-row insert
in the same Postgres transaction, one round-trip to one datastore.
`RedisHoldStrategy.acquire_hold` does its `SET NX EX` against Redis *and*
still needs the identical Postgres `Booking`-row insert — an extra network
hop to a second service on every single request, in this specific
single-machine Docker Compose topology where Redis and Postgres are
separate containers reached over the same loopback-routed bridge network.
This is an environment-shaped cost, not an indictment of Redis's
correctness mechanism (`SET NX EX` is not doing anything slower than
Postgres's row lock at the operation level) — it is the honest, measured
result of adding a service hop that the cron strategy's design avoids by
construction. A deployment where Redis and Postgres sit at meaningfully
different network distances from `booking-service` (e.g. one co-located,
one not) could shift this number in either direction; this benchmark
measures the topology it was actually run against, not a hypothetical one.

### Release latency — passive path (abandonment → sweep/TTL)

| Metric | cron | redis |
|---|---|---|
| Measured release latency | 11.06s | 12.05s |

Both fall inside their expected TTL-plus-one-sweep-interval window
(`HOLD_TTL_SECONDS=10` + `HOLD_SWEEP_INTERVAL_SECONDS=5` → up to 15s
worst case for cron's sweep-triggered release; Redis's own key TTL plus
one `expire_stale_pending()` sweep interval for the `Booking` row →
similarly up to 15s). cron is again marginally faster here (~8% less),
consistent with the same "one fewer service hop" pattern as the
acquisition numbers, though the gap is small enough relative to the
5-second sweep granularity that it is better read as "both strategies'
passive release is bounded by the *configured* TTL/sweep interval, not by
a meaningful difference in mechanism speed" — this metric is dominated by
a chosen configuration knob, not by cron vs. Redis as such.

### Release latency — immediate trigger (§17)

| Metric | cron | redis |
|---|---|---|
| Trigger-write time | 5.1ms | 5.9ms |
| Time to observed release | <1ms | <1ms |

Both strategies release in single-digit milliseconds when triggered
directly rather than waiting on the passive path — roughly **three orders
of magnitude faster** than the ~11-12 second passive numbers above,
independent of which hold strategy is active. This is the strongest,
least ambiguous result in the whole benchmark: it confirms decisions-log
§17's compensation-flow decision (build a real `payment.failed` →
immediate-release path, not just rely on the timeout safety net) is worth
its documented implementation cost (§17: "roughly 2-4 days for the
retry/DLQ pattern, plus ~0.5-1 day for the payment-failure hold-release
handler") regardless of which hold strategy eventually ships, since the
gap between "customer sees the seat freed in milliseconds" and "customer
waits up to a sweep interval" holds either way.

## Honest reading of the comparison

Per the kickoff doc's own instruction: if one strategy wins cleanly across
every metric, say so plainly rather than manufacturing a more balanced
story than the data supports. **In this benchmark, cron won on every
measured metric** — acquisition latency (all percentiles), passive release
latency, and immediate-release trigger time. That is a genuinely clean
result, not a mixed one, and it should be reported as such.

It does not, however, mean "cron is unconditionally the better strategy" —
that would overclaim past what a single-machine, single-`booking-service`-
instance benchmark can support. What it does show, precisely: **at this
benchmark's scale and topology, the network hop Redis adds to every
acquisition is a real, measurable cost that Redis's `SET NX EX` mechanism
itself does not recoup anywhere in this test.** The scenario where Redis's
usual advantages would be expected to show up — taking sweep/lock
contention load off the primary relational database, or scaling the lock
layer independently of Postgres connection-pool pressure — requires either
a much larger seat pool/contention level, multiple concurrent
`booking-service` instances sharing load, or a topology where Postgres
itself is closer to saturated than it was at 300 requests against 30
seats. None of those conditions held here; this benchmark measured a
single moderate burst against a single service instance, which is exactly
the regime where an extra network hop shows up as pure overhead with
nothing to offset it.

**Given the two strategies were also already established as equally
correct** (P3.T7, and reconfirmed by this run's exact 30/30 successful
allocation under both), and cron requires no second infrastructure
dependency (no Redis container, one fewer moving part in the deployed
system, §12's cost-management priorities), the measured evidence in this
report favors `cron` as the default for the current single-instance
deployment target (§12: AWS Elastic Beanstalk, one instance). Redis
remains the documented alternative for a future multi-instance topology
where its independent-scaling property would have a chance to pay for the
hop cost measured here — a Future Work note (§26), not a claim this
benchmark can make on its own.
