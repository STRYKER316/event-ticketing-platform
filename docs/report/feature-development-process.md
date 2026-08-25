# Feature Development Process

*Status: Measured (P8, 2026-08-16). This chapter's centerpiece — the
cron-vs-Redis hold-mechanism comparison — is the report's only chapter
built on real load-test execution rather than unit/integration test
results alone. Every number below comes from `docs/benchmark-results/`,
archived raw output from `benchmark/run_benchmark.py` run against the live
compose stack. Per CLAUDE.md's Integrity rule, nothing here is estimated
or placeholder.*

*Revision note: a dedicated adversarial `/code-review` pass and an
independent `/pre-pr` review both caught a real measurement bug in the
first version of this analysis — the harness's HTTP client had a default
100-connection cap that silently throttled the 300-request burst,
counting client-side queueing as "hold-acquisition latency." Fixed, and
every run below is from the corrected harness, repeated three times per
strategy rather than once (a single run cannot support a "clean win"
claim). The corrected numbers tell a different, more mixed story than the
first version did — see `docs/benchmark-results/README.md`'s revision
note for the full account.*

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
load — a genuine engineering trade-off, deliberately left open in
decisions-log §6 until it could be measured rather than guessed.

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

**HTTP client connection limit sized to the load profile.** `httpx`'s
default `AsyncClient` caps at 100 concurrent connections; against a
300-request burst, roughly two-thirds would queue for a free connection
before reaching the server. Since `attempt_booking`'s timer starts before
the request is sent, that queueing time would be counted as server-side
"hold-acquisition latency" rather than the client-side artifact it
actually is. Fixed by sizing `httpx.Limits` to the load profile
(`seat_pool_size * clients_per_seat + 20`), so every request in the burst
can be in flight simultaneously, the same way 300 independent real
clients would be.

**Each run repeated three times per strategy, not once.** A single run
cannot distinguish "strategy A is genuinely faster" from "run-to-run
noise happened to favor A" — this matters here specifically because the
first version of this benchmark (a single run each) reported a clean
cron win that a second and third run did not reproduce (see the revision
note above). All aggregate numbers below are mean and range across three
independent runs per strategy.

**Three metrics measured, per run:**
1. Successful/failed booking counts under the contention burst.
2. Hold-acquisition latency (p50/p95/p99) for successful bookings.
3. Release latency, two ways: the *passive* path (an abandoned hold left
   to expire via cron sweep interval / Redis TTL, polled for), and the
   *immediate* path (the `payment.failed` → immediate-release trigger from
   §17, simulated directly — see the decisions-log §17 amendment for why: P4,
   the real trigger's producer, doesn't exist yet at this point in the
   locked report-first build order, and deferring the whole metric until
   P4 would contradict the reason P8 runs before P4 in the first place).

Full command, raw archived output, and the exact restart procedure between
strategies: `docs/benchmark-results/README.md`.

## Results

All figures are mean (range) across three independent runs per strategy,
identical load profile. Full per-run data: `docs/benchmark-results/`.

### Contention burst — hold acquisition

| Metric | cron | redis |
|---|---|---|
| Successful bookings (of 30 contended seats), each run | 30/30 (all 3 runs) | 30/30 (all 3 runs) |
| Failed bookings, each run | 270/270, all real `409`s | 270/270, all real `409`s |
| Hold-acquisition latency, p50 | 0.431s (0.267–0.524) | 0.446s (0.279–0.702) |
| Hold-acquisition latency, p95 | 1.052s (0.731–1.233) | 1.257s (1.074–1.559) |
| Hold-acquisition latency, p99 | 1.144s (0.830–1.333) | 1.417s (1.252–1.660) |

Both strategies allocated the pool exactly correctly, every run — 30
winners, one per seat, 270 losers, all real `409`s (verified via the
harness's status-code breakdown, not just an aggregate failure count) —
under identical 10-way-per-seat contention. This matches, rather than
merely repeats, P3.T7's correctness proof: that test established *that*
exactly one client wins; this run establishes *how fast* winners and
losers alike are told the outcome, at a scale (300 concurrent requests)
an order of magnitude past P3.T7's 25.

**p50 is statistically indistinguishable between strategies at n=3** — the
ranges overlap almost entirely (cron 0.267–0.524s, redis 0.279–0.702s),
and the means (0.431s vs. 0.446s) differ by less than either strategy's
own run-to-run variance. p95/p99 show a mild, consistent trend toward
redis running slightly slower, but three samples is not enough to call
that trend significant rather than noise. The honest conclusion: **at
this benchmark's scale (300 requests, single machine, single
`booking-service` instance), hold-acquisition latency does not
meaningfully distinguish the two strategies.** Both mechanisms —
`CronHoldStrategy`'s atomic Postgres `UPDATE` and `RedisHoldStrategy`'s
`SET NX EX` plus the shared `Booking`-row insert — resolve fast enough
relative to the request's other overhead (network, auth, FastAPI
dispatch) that the difference between them gets lost in ordinary
variance at this load level, not clearly demonstrated by it.

### Release latency — passive path (abandonment → sweep/TTL)

| Metric | cron | redis |
|---|---|---|
| Measured release latency | 12.07s (11.07–13.08) | 12.73s (11.05–14.07) |

Both fall inside their expected TTL-plus-one-sweep-interval window
(`HOLD_TTL_SECONDS=10` + `HOLD_SWEEP_INTERVAL_SECONDS=5` → up to 15s
worst case for either mechanism's sweep-triggered release). The means are
close enough (12.07s vs. 12.73s, a 5% gap smaller than the 5-second sweep
granularity itself) that this metric is dominated by the *configured*
TTL/sweep interval, not by a meaningful difference in mechanism speed —
both strategies' passive release is, correctly, bounded by whatever
interval an operator configures, not by which strategy is active.

### Release latency — immediate trigger (§17, simulated)

| Metric | cron | redis |
|---|---|---|
| Trigger-write time | 4.28ms (3.58–4.89) | 6.69ms (4.87–8.42) |

This is the one metric where redis was slower in **all three** runs, not
just on average — a small but consistent gap, plausibly the same extra
network hop (a separate Redis container) discussed for acquisition
latency above, though at this magnitude (single-digit milliseconds) three
samples is suggestive rather than conclusive. What is conclusive,
regardless of which strategy: **both release in single-digit
milliseconds when triggered directly, versus ~12 seconds via the passive
path — roughly three orders of magnitude faster, independent of hold
strategy.** This confirms the compensation-flow decision in decisions-log
§17 (build a real `payment.failed` → immediate-release path, not just rely on
the timeout safety net) is worth its documented implementation cost (§17:
"roughly 2-4 days for the retry/DLQ pattern, plus ~0.5-1 day for the
payment-failure hold-release handler") regardless of which hold strategy
eventually ships.

**Phase 4 built the real mechanism this table's numbers simulated.**
`PaymentOutcomeConsumer` (`booking-service/app/kafka/consumers.py`)
performs exactly the write this benchmark measured directly — the
`release_hold()` call this table's "trigger-write time" timed is no longer
a stand-in for a future handler, it's the real one, verified live against
the running stack (see the Testing Strategy documentation (Appendix A) and
the Class Diagrams chapter's Payment Service section). The numbers above
were not re-measured, since
the mechanism is identical either way (the original amendment to §17 already
established this — simulating the call is a faithful measurement of the
mechanism, not a placeholder for it).

## Honest reading of the comparison

Per the kickoff doc's own instruction: report a genuinely mixed result as
mixed rather than manufacturing a cleaner story than the data supports —
and this is a genuinely mixed result. **Hold-acquisition latency and
passive release latency do not meaningfully distinguish cron from redis
at this benchmark's scale; immediate-release trigger time shows a small,
consistent edge for cron, on the order of a couple of milliseconds.**

This report's *first* draft, built from a single run per strategy, claimed
cron won cleanly on every metric. Running each strategy twice more
changed that conclusion — the acquisition-latency "win" didn't reproduce;
only the millisecond-scale trigger-write gap did. That reversal is itself
worth stating plainly: it is direct evidence for why this phase's own
process note ("never fabricate, estimate, or placeholder a number") and
n=1 measurements are a bad combination — a single sample can look like a
clean result purely by chance, and the fix wasn't a better story, it was
more data.

**Given the two strategies were also already established as equally
correct** (P3.T7, and reconfirmed by this run's exact 30/30 successful
allocation under both strategies, all three runs each), and cron requires
no second infrastructure dependency (no Redis container, one fewer moving
part in the deployed system, per the resource-budget consolidation reasoning
in §12), the
measured evidence in this report mildly favors `cron` as the default for
the current single-instance deployment target (§12: AWS Elastic
Beanstalk, one instance) — not because it demonstrably outperforms redis
at this scale (it doesn't, on the numbers), but because it wins the one
metric that did show a consistent difference (immediate-release
trigger time) and carries one fewer infrastructure dependency, with
neither number strong enough to call the case closed. Redis remains a
reasonable alternative, particularly for a future multi-instance topology
where its independent-scaling property (taking lock contention off the
primary relational database) has a chance to matter in a way this
single-instance, 300-request benchmark could not exercise — a Future Work
note (§26), not a claim this benchmark can make on its own.
