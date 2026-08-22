# Phase 9 Kickoff — Hardening

**Goal of this phase:** close integration-test gaps, verify idempotency
invariants system-wide, and tidy observability and edge cases (master plan
Phase 9). This is not new architecture — every mechanism this phase touches
(idempotent consumers, ownership-scoped guards, rowcount-gated transitions)
was already decided and built in earlier phases. Phase 9's job is to *prove*
those mechanisms hold under redelivery/race conditions with real tests, and
fix anything an honest audit finds missing, per the Integrity rule: a
guard that exists in code but has no test backing it cannot be called
"Tested."

**How to use this file:** run the four tasks below in order, one per Claude
Code session. Commit after each (small, green commits). `main` stays
bootable at every step.

**Entry deps:** P5 (Notification), P6 (Search consolidation), P7 (Frontend),
P8 (Benchmark) all complete per the locked build order (decisions-log §27).

**Pre-read audit (done as part of writing this kickoff doc, not deferred to
P9.T1 itself):** before drafting task prompts, the current state of each
task's scope was checked against the actual repo rather than assumed blank.
Findings below shape each task's prompt — treat "already covered" items as
verification/consolidation, not rebuild-from-scratch:

- **P9.T1 scope.** 4 of 5 Kafka integration points (§7) already have an
  explicit redelivery-is-a-no-op test from the phase that built them:
  Event→Search (`test_event_consumer_flow.py`, both upsert and delete),
  Event→Booking (`test_provisioning_consumer.py`,
  `test_redelivered_message_creates_no_duplicate_tickets`),
  Booking/Payment→Notification (`test_notification_flow.py`,
  `test_redelivery_of_same_message_is_a_safe_no_op`), and
  Booking→Payment/refund (`test_refund_flow.py`,
  `test_booking_cancelled_consumer_redelivery_is_a_safe_no_op`, plus a
  manager-level replay test). The one gap: Payment→Booking's
  `test_payment_outcome_consumer.py` only tests a *stale cross-message*
  scenario (a late "failed" arriving after the booking already went
  CONFIRMED) — never a literal identical-message-delivered-twice case, the
  same shape every other point already has.
- **P9.T2 scope.** All 5 services already share the same `structlog`
  scaffolding (copied from the `event-service` template, per CLAUDE.md
  Conventions) and `/metrics` via `prometheus-fastapi-instrumentator` since
  P0.T5. The audit is a *consistency* check — log-level discipline against
  CLAUDE.md's debug/info/warning/error rule, and confirming `/metrics`
  parity — not new instrumentation from scratch.
- **P9.T3 scope.** Two of the three named edge cases already have both a
  correctness guard and a test: webhook replay
  (`test_payment_flow.py::test_webhook_transitions_payment_and_replay_is_a_safe_no_op`)
  and the expired-hold race under concurrent acquisition
  (`test_cron_hold_race.py`, `test_concurrency_suite.py`). Two real gaps
  found: (a) double-cancelling an already-CANCELLED booking — the guard
  exists (`BookingRepository.transition_if_confirmed`, used by
  `cancel_booking`, rejects with 409 if the booking isn't currently
  CONFIRMED) but has no test; (b) paying against a booking whose hold
  already expired/lost its race — the guard exists
  (`_fetch_owned_booking_in_status(..., BookingStatus.PENDING, ...)` in
  `pay_booking`, 409s if the booking moved off PENDING) but also has no
  test. Both are "prove the existing guard works," not new mechanism.
- **P9.T4 scope.** `make seed` exists (`services/event-service/app/seed.py`)
  but is idempotent-skip-only — it no-ops if any event already exists,
  it does not reset state. `make down` tears down containers but not
  volumes. There is no single command that returns the stack to a known,
  reproducible demo state after a demo run has accumulated bookings/holds/
  payments. This is a genuine gap, not just an audit finding.

**No open questions requiring a user decision this phase** — unlike P8
(k6-vs-Python, payment-dependency timing), every task here has a clear,
already-decided mechanism to verify or a bounded gap to fill. No new
Kafka points, no new services, no schema changes.

**Process note specific to this phase (per `CLAUDE.md`):** no dedicated
adversarial `/code-review` pass is required here (that's reserved for P3
and P8 specifically) — the routine `/pre-pr` gate at CHECKPOINT plus
self-verification is sufficient, same as every phase besides P3/P8.

---

## P9.T1 — Kafka idempotency integration-test sweep

**Prompt to Claude Code:**
> Add the one missing literal-redelivery test to
> `services/booking-service/tests/integration/test_payment_outcome_consumer.py`
> — call `consumer._handle()` twice with the *identical* raw "succeeded"
> message bytes (mirroring the shape `test_provisioning_consumer.py`'s
> `test_redelivered_message_creates_no_duplicate_tickets` and
> `test_refund_flow.py`'s `test_booking_cancelled_consumer_redelivery_is_a_safe_no_op`
> already use) and assert the booking is CONFIRMED exactly once, the ticket
> is BOOKED exactly once, and the notification producer is called exactly
> once — not twice. Then write a short coverage matrix (5 Kafka points ×
> redelivery test reference) into the testing-strategy report chapter,
> citing the actual test names for all five points, not just a claim that
> they're idempotent.

**Done when:** the new test passes against a real Kafka/Postgres/Redis
testcontainer stack, and all five points have a named, citable redelivery
test. Report evidence: testing-strategy chapter's Kafka-idempotency
subsection.

---

## P9.T2 — Logging + `/metrics` consistency audit

**Prompt to Claude Code:**
> Read every `logger.debug/info/warning/error/critical` call site across
> all 5 services and check each against CLAUDE.md's log-level discipline
> rule (debug = normal flow/tracing, info = notable events, warning =
> expected/handled failures such as validation rejections or business-rule
> raises, error/critical = genuine incidents only — never log a routine
> rejected-input path at error). Fix any misclassified call sites found.
> Separately, confirm all 5 services expose `/metrics` and that Prometheus
> (via the `bench-up` profile) can actually scrape all 5, not just the ones
> exercised during P8's benchmark work (which only needed `booking-service`
> under the microscope). Document findings (what was audited, what was
> fixed, if anything) in the observability report chapter.

**Done when:** every log call site is correctly leveled per the rule above,
and `/metrics` is confirmed reachable and scraped for all 5 services with
the benchmark profile up. Report evidence: observability writeup.

---

## P9.T3 — Edge cases: double-cancel, expired-hold race, webhook replay

**Prompt to Claude Code:**
> Two of these three already have tests (webhook replay in
> `test_payment_flow.py`, expired-hold race in `test_cron_hold_race.py` /
> `test_concurrency_suite.py`) — confirm they still pass and reference them
> rather than duplicating coverage. Add the two missing tests: (1) a
> double-cancel test — cancel a booking, then attempt to cancel it again,
> asserting the second call 409s via `transition_if_confirmed`'s existing
> guard and does *not* publish a second `booking.cancelled` message; (2) a
> pay-after-hold-lost test — put a booking into a non-PENDING state (either
> by letting its hold expire via the sweep/TTL path, or by directly driving
> it to EXPIRED the way `test_payment_outcome_consumer.py`'s failed-message
> test already does) and assert `pay_booking` 409s via
> `_fetch_owned_booking_in_status`'s existing guard rather than initiating
> a charge against a booking nobody can legitimately complete anymore.

**Done when:** all three edge cases have a passing, citable test — the two
new ones plus confirmation the two existing ones still hold. Report
evidence: none directly (feeds the same testing-strategy chapter as P9.T1).

---

## P9.T4 — Seed/reset scripts polished for a clean demo

**Prompt to Claude Code:**
> Add a `make reset` (or equivalently named) target that returns the local
> stack to a known-good demo state in one command — tear down and recreate
> the Postgres/Mongo/Redis volumes (or truncate the relevant tables/
> collections directly, whichever is faster and still correct against a
> running stack), re-run migrations, and re-run `make seed` against the now-
> empty state. Document the exact command and what it does/does not touch
> (e.g. does it also clear Keycloak's realm data, or is that left alone
> since it's not demo-accumulated state) in `infra/README.md` or wherever
> the existing `make` targets are documented.

**Done when:** running the new target against a stack that has accumulated
demo bookings/holds/payments from a prior run returns it to the same
known-good state a fresh `make up && make migrate && make seed` would
produce, verified live. Report evidence: none directly.

---

## Phase 9 exit checklist (all must pass before P10)

- [x] P9.T1 — missing literal-redelivery test added for Payment→Booking
      (`test_redelivered_succeeded_message_confirms_and_notifies_exactly_once`);
      all 5 Kafka points have a named, citable redelivery test; coverage
      matrix written into `docs/report/testing-strategy.md`.
- [x] P9.T2 — log-level audit complete across all 5 services (67 call
      sites read against CLAUDE.md's rule); one real misclassification
      found and fixed (`payment_manager.py`'s Stripe charge-failure log,
      aligned to its already-correct refund-failure sibling); `/metrics`
      confirmed scraped for all 5 services live via Prometheus's targets
      API (`GET /api/v1/targets`, all `health: "up"`); observability
      writeup in `docs/report/technologies-used.md`.
- [x] P9.T3 — double-cancel
      (`test_double_cancel_second_call_409s_and_does_not_re_release_or_republish`)
      and pay-after-hold-lost
      (`test_pay_booking_after_hold_expired_via_real_sweep_409s_without_charging`)
      tests added and passing against real Postgres testcontainers;
      webhook-replay and expired-hold-race coverage re-run and confirmed
      still green.
- [x] P9.T4 — `make reset` added (`infra/reset-demo-state.sh`),
      live-verified against this project's own real accumulated demo
      state (15 events/20 bookings/24 tickets/14 payments from prior
      sessions), re-run twice to confirm idempotency, documented in
      `infra/README.md`.
- [x] Full suite green (unit + integration) across all 5 services after
      this phase's changes: event-service 55/55, search-service 18/18,
      booking-service 80/80, payment-service 22/22, notification-service
      11/11.
- [x] Live walkthrough done at CHECKPOINT — no new user-facing surface
      this phase (backend hardening only), so the walkthrough took the
      form of live verification against the real running stack throughout:
      Prometheus/Traefik APIs queried live, `make reset` run twice against
      real accumulated data, all five suites re-run against real
      containers. `docs/architecture.html` updated to current state
      (§06 checklist, narrative paragraph, badges); `docs/build-log.md`
      entries appended for P9.T1–T4 plus the CHECKPOINT `/pre-pr` pass.
- [x] `decisions-log.md` delta check — explicitly checked against every
      task: none needed. §11 (Logging/Monitoring) already said "every
      service exposes `/metrics`" without claiming gateway routing, so
      the P9.T2 report correction didn't contradict it; §19 (seed data)
      isn't contradicted by `make reset`, only extended in the same
      no-DB-decisions-touched way P9.T1/T3's test additions were.
- [x] `CLAUDE.md` self-update check — explicitly checked: no new
      per-service pattern, no new Makefile-target-tracking convention
      (Makefile targets are documented in `infra/README.md`, already
      updated), log-level-discipline rule was audited against, not
      created. No update needed.
- [x] Phase-end checklist item 7 (`/pre-pr`) run against the diff since
      this phase's starting commit (`0e2b770..HEAD`): simplify deduped
      `reset-demo-state.sh`'s three truncate blocks; a dedicated `opus`
      code-review found 9 issues (2 stale/wrong figures, a doc
      self-contradiction, a wrong cross-reference, a real ES
      status-check bug masking failures under `set -e`, a test
      order-dependency, missing build-log entries, and a commit-message
      rule violation) — all fixed except the commit-message violation,
      deliberately left for the before-push checklist rather than a
      risky late-session history rewrite (see `docs/build-log.md`'s
      CHECKPOINT entry for the reasoning). All fixes live re-verified.
- [x] Phase-end checklist item 8 (cross-doc staleness sweep) run against
      what this phase's diff actually touched: `infra/README.md`'s stale
      4-service Prometheus claim (predates this phase, found while
      touching the same paragraph for P9.T2) fixed alongside it;
      `docs/report/README.md`'s chapter-status table updated for both
      `testing-strategy.md` and `technologies-used.md`.
- [x] This file's own exit checklist checked off with evidence notes, per
      `CLAUDE.md`'s phase-end checklist item 9 — done in this same pass,
      not deferred.

**Report evidence captured this phase (§16):** testing-strategy chapter's
Kafka-idempotency coverage matrix, observability writeup.

**Next:** Phase 10 — AWS Elastic Beanstalk Deployment, per the locked
report-first build order.
