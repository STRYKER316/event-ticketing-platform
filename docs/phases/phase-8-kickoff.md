# Phase 8 Kickoff — Hold-Mechanism Benchmark (report centerpiece)

**Goal of this phase:** the real, measured cron-vs-Redis comparison (§6),
plus the release-latency metric (§17). This is the report's only **Measured**
chapter — every other chapter can be honestly labeled Implemented/Tested/
Verified from real execution in this repo, but only this phase produces
numbers earned by actually running load against the system and recording
what happened.

**How to use this file:** run the six tasks below **in order**, one per
Claude Code session. Commit after each (small, green commits). `main` stays
bootable at every step. Give Claude Code the repo plus `decisions-log.md`
and `master-development-plan.md` as context so it stays anchored to the
locked decisions (referenced by § below) — and this file itself, since it
records one open question (see below) that needs resolving before P8.T5.

**Entry deps:** Phase 3 complete — both `TicketHoldStrategy` implementations
exist, pass the shared concurrency contract, and are checkpointed (two
review passes, live-verified, pushed to `origin/main`). Nothing else is
required; per the locked report-first build order (decisions-log §27), P8
runs immediately after P3 and *before* Payment Service (P4) specifically
because it needs only Booking's two hold strategies — see the open question
below for the one place that assumption gets tested.

**Open question to resolve before starting P8.T5 (read this first):**
P8.T5 asks to measure "release-latency: immediate-on-failure vs. timeout"
(§17). §17's actual immediate-release trigger is a `payment.failed` Kafka
event that only Payment Service (P4) will ever publish — and P4 doesn't
exist yet at this point in the build order. Two ways to resolve this,
neither silently assumed by this doc:
- **(a) Simulate the "immediate" trigger directly** — call
  `TicketHoldStrategy.release_hold()` (or a thin booking-service-internal
  path to it) at a known instant, bypassing the not-yet-built Kafka
  consumer entirely, and measure how fast the resulting state change
  (`Ticket` back to `AVAILABLE`) is observable, compared against how long
  an abandoned hold takes to clear via the *passive* path (cron sweep
  interval / Redis TTL). This is what "needs only Booking's two hold
  strategies" (§27) most likely intended, since the release mechanism
  itself is identical either way — P4 only changes *what calls it*, not
  how fast release itself is once called.
- **(b) Defer P8.T5 specifically** until P4 exists, running P8.T1-T4/T6 now
  and coming back for the release-latency metric once the real
  `payment.failed` → release path is wired, either as a P8 addendum or
  folded into P4's own checkpoint.
This doc doesn't pick one — decide explicitly at the start of the P8.T5
session (ideally by asking the user) and record the choice in
`decisions-log.md` §17 as an amendment, the same way the P1/P3 amendments
were recorded, rather than letting it be an implicit, undocumented call.

**Open question to resolve before P8.T2:** decisions-log §6 says "k6 or a
multi-threaded harness" — never decided which. Two real options:
- **k6** — purpose-built load-testing tool, richer built-in metrics/
  reporting, but a new (Go-based) dependency this project hasn't touched
  anywhere else.
- **A Python asyncio harness** — reuses the exact `asyncio.gather`-based
  concurrent-client pattern already proven in P3.T7's concurrency suite
  (`tests/integration/test_concurrency_suite.py`), no new tooling, but
  metrics collection/reporting has to be hand-rolled rather than coming
  built-in.
Recommendation, not a decision: the Python harness, since it's a smaller
addition to a project that's Python end-to-end everywhere else and the
concurrency pattern is already battle-tested in this exact codebase — but
confirm with the user before committing to it, since it affects the
Technologies Used report chapter either way.

**Process note specific to this phase (per `CLAUDE.md`):**
- **Never fabricate, estimate, or placeholder a number.** This is the one
  phase where that isn't a general-integrity reminder but the literal
  point of the phase — every number in P8.T3/T4/T5's output has to come
  from an actual run against the actual system, archived raw, before it's
  allowed anywhere near the report.
- A **dedicated adversarial `/code-review` pass** runs at this phase's
  CHECKPOINT, on top of the routine `/pre-pr` gate and self-verification —
  same as Phase 3, not instead of either. For P8 specifically, the review
  should scrutinize the benchmark harness itself as hard as the code under
  test: a harness bug that silently undercounts failures or mismeasures
  latency would corrupt the report's only Measured chapter just as badly
  as a real hold-strategy bug would.

---

## P8.T1 — Prometheus + Grafana compose profile

**Prompt to Claude Code:**
> Add a `docker-compose` profile (§11, §24) for Prometheus + Grafana,
> brought up on demand for benchmark runs rather than as part of the
> default `make up` stack. Prometheus scrapes every service's existing
> `/metrics` endpoint (already wired via `prometheus-fastapi-instrumentator`
> since P0.T5 — no new instrumentation needed, just the scraping
> infrastructure). Grafana gets at least one dashboard that can render
> request-rate/latency for `booking-service` specifically, since that's
> the service under test.

**Done when:** metrics are scraped from all services with the profile up;
a Grafana dashboard renders real data from a running stack. Report
evidence: none directly — this is enabling infrastructure for P8.T3/T4.

---

## P8.T2 — k6 (or Python) load-test harness

**Prompt to Claude Code:**
> Resolve the k6-vs-Python-harness open question above first (with the
> user), then build the concurrent-client load harness (§6): N clients
> repeatedly attempting to book seats from a small, fixed seat pool against
> a running `booking-service`, parameterized by which `HOLD_STRATEGY` is
> active so the identical script runs against both. Metrics to capture per
> run: successful/failed booking counts, hold-acquisition latency
> (p50/p95/p99, not just mean), and time-to-release-after-abandonment.
> Script must be re-runnable on demand with a fixed, documented command and
> a fixed load profile (client count, ramp pattern, seat-pool size) — the
> Validation checkpoint below depends on this being reproducible, not a
> one-off.

**Done when:** the script runs end-to-end against a live `booking-service`
and emits all three required metrics in a structured, archivable format.
Report evidence: benchmark methodology writeup (what's measured, how, why
this load profile).

---

## P8.T3 — Run the cron strategy under load

**Prompt to Claude Code:**
> Boot the stack with `HOLD_STRATEGY=cron`, run the P8.T2 harness against
> it at the fixed load profile, and archive the raw output (successful/
> failed counts, latency percentiles, time-to-release) plus a Grafana
> screenshot/export under `/docs` — not just a summary number, the actual
> raw run data, so the report's numbers are traceable back to a real
> execution rather than a paraphrase of one.

**Done when:** raw results are archived in `/docs` in a form the P8.T6
analysis can cite directly. Report evidence: Measured results (cron).

---

## P8.T4 — Run the Redis TTL strategy under identical load

**Prompt to Claude Code:**
> Same as P8.T3, but `HOLD_STRATEGY=redis`, same fixed load profile (client
> count, seat-pool size, ramp pattern — identical to P8.T3's run, this is
> what makes the comparison valid), same archiving discipline.

**Done when:** raw results are archived, directly comparable to P8.T3's
(same load profile, same metrics). Report evidence: Measured results
(Redis).

---

## P8.T5 — Release-latency metric: immediate-on-failure vs. timeout

**Prompt to Claude Code:**
> Resolve the payment-dependency open question above first (with the
> user) — this task cannot start until that's decided and recorded as a
> decisions-log §17 amendment. Then measure and archive both numbers: how
> long an *immediately triggered* release takes to become observable
> (`Ticket` back to `AVAILABLE`), versus how long the *passive* path takes
> under each strategy (cron sweep interval, Redis TTL) — same archiving
> discipline as P8.T3/T4.

**Done when:** both numbers are measured (not estimated) and archived.
Report evidence: second measured metric, feeds the same Feature
Development Process chapter as P8.T3/T4.

---

## P8.T6 — Analysis writeup

**Prompt to Claude Code:**
> Write the honest, strategy-vs-strategy comparison from P8.T3/T4/T5's
> archived raw data — which strategy wins on which metric, by how much,
> and why (tie the numbers back to the actual mechanism difference already
> documented in the Class Diagrams and Technologies Used chapters: one
> atomic conditional Postgres `UPDATE` vs. Redis's `SET NX EX`). If the
> results are close, or if one strategy wins on throughput but loses on
> release-latency (or vice versa), say so plainly — a genuinely mixed
> result is more credible and more citable than a suspiciously clean win
> for one side, and CLAUDE.md's Integrity rule applies here as much as
> anywhere else in this project.

**Done when:** draft is ready to drop into the Feature Development Process
report chapter. Report evidence: **Feature Development Process chapter
core** (§16) — this is the single most important writeup in the whole
report; everything else in this phase exists to produce the data this task
turns into prose.

---

## Phase 8 exit checklist (all must pass before P4)

- [x] Both open questions above resolved and recorded (k6-vs-Python choice
      needs no decisions-log entry; the payment-dependency choice for P8.T5
      does, as a §17 amendment). k6-vs-Python resolved with the user
      2026-08-16, Python asyncio chosen; §17 amendment recorded (simulate
      the trigger directly), later corrected in place with n=3 numbers
      after the connection-limit bug fix.
- [x] Prometheus + Grafana compose profile working, scraping real metrics.
      Live-verified twice: initial P8.T1 verification (all 3 targets `up`,
      real dashboard panel data via Grafana's datasource-proxy API) and
      again during the CHECKPOINT walkthrough (`make up` + `make bench-up`
      from a clean state, confirmed `up`).
- [x] Load harness reproducible: fixed documented command, fixed load
      profile, re-runnable on demand. `benchmark/run_benchmark.py`,
      documented in `benchmark/README.md`; re-run 6 times (3x cron, 3x
      redis) with byte-identical parameters, confirming reproducibility.
- [x] Cron strategy run archived (raw data + graphs), Redis strategy run
      archived under the identical load profile. `docs/benchmark-results/
      {cron,redis}-run-{1,2,3}.json` (raw per-request data + summaries) and
      `{cron,redis}-grafana-export.json` (dashboard panel data via API,
      screenshot unavailable — browser extension not connected this
      session, see `docs/benchmark-results/README.md`).
- [x] Release-latency metric measured and archived for both the immediate
      and passive paths. Both present in every archived run file
      (`release_latency` and `immediate_release_latency` keys); the
      immediate path required simulating the not-yet-built `payment.failed`
      trigger directly, per the §17 amendment.
- [x] Validation checkpoint done live: benchmark is reproducible, raw
      outputs and graphs are archived, and the comparison is drawn only
      from measured data — no placeholder or estimated number anywhere in
      the archived results or the analysis writeup. A real measurement bug
      (HTTP client connection-pool cap contaminating latency) was caught by
      the dedicated review before this box was checked, fixed, and every
      number re-measured (not patched) — see the CHECKPOINT build-log
      entry. n=3 runs per strategy, not n=1, specifically because a single
      run cannot support the comparison's conclusions.
- [x] Dedicated adversarial `/code-review` pass run on top of the routine
      `/pre-pr` gate and self-verification (per this phase's process note
      above) — not a substitute for either, and specifically scrutinizing
      the harness's own correctness, not just the code under test. Ran
      both: dedicated pass found the connection-limit bug (confirmed),
      a Grafana "no data" claim (traced against library source and
      rejected as a false positive), and four other real findings, all
      fixed and re-verified live. `/pre-pr`'s own code-review step
      independently found the same connection-limit bug.
- [x] Live walkthrough done at CHECKPOINT; `docs/architecture.html` updated
      to current state; `docs/build-log.md` entry appended; decisions-log
      delta logged if any. `docs/architecture.html` §07 added and later
      corrected to match the n=3 numbers; `make down`/`make bench-up`/
      `make bench-down` all live-verified in both states (profile up and
      never started) as part of this same pass; decisions-log §6/§17
      amended (§17 corrected in place after the re-measurement).
- [x] This file's own exit checklist checked off (per `CLAUDE.md`'s
      phase-end checklist item 9), not left for a later session to notice
      is still unchecked. Checked off with evidence notes in this same
      CHECKPOINT pass, not deferred.
- [x] Phase-end checklist item 7 (`/pre-pr`) run against the diff since
      this phase's starting commit, findings self-applied. Simplify step
      applied 4 real fixes (and introduced 1 regression, caught by
      smoke-test and fixed); code-review step's findings applied; verify
      step folded into the live re-runs and walkthrough above rather than
      run separately, since every endpoint this diff touches was already
      exercised live multiple times over.

**Report evidence captured this phase (§16):** benchmark methodology
writeup, Measured results (cron), Measured results (Redis), second
measured metric (release latency), and the Feature Development Process
chapter's core analysis writeup — the report's centerpiece.

**Next:** Phase 4 — Payment Service + Stripe + Confirmation (Kafka #4),
per the locked report-first build order. If the P8.T5 open question above
was resolved as option (b) (deferred), Phase 4's own kickoff should note
the still-open release-latency measurement as unfinished P8 business, not
silently drop it.
