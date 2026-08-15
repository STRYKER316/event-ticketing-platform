# Phase 3 Kickoff — Booking Service + Provisioning + Dual Hold

**Goal of this phase:** the double-booking-critical service. A new Booking
Service, its own Postgres DB (`booking_db`), ticket provisioning from Kafka,
and **both** hold strategies behind a `TicketHoldStrategy` interface (§6).
This is the largest phase and the report's centerpiece — the Phase 8
benchmark (run immediately after this phase, per the report-first build
order) measures the two strategies this phase builds.

**How to use this file:** run the seven tasks below **in order**, one per
Claude Code session. Commit after each (small, green commits, per the
per-service layering conventions in `CLAUDE.md`). `main` stays bootable at
every step. Give Claude Code the repo plus `decisions-log.md` and
`master-development-plan.md` as context so it stays anchored to the locked
decisions (referenced by § below).

**Entry deps:** Phase 2 complete (Event Service publishes on publish/
update-while-published/delete; Search Service consumes idempotently). The P1
addendum (venue/seat-map write API) and the pre-Phase-3 adversarial testing
pass are both done — see `docs/build-log.md` for what they closed, including
a concurrent-delete race in `EventRepository`/`BaseRepository` fixed by
switching to a rowcount-checked Core-level `DELETE`. That's the same
check-then-act race class this phase's seat-hold acquisition has to defeat
at much higher stakes — worth having read before P3.T6/T7.

**Process note specific to this phase (per `CLAUDE.md`):**
- **Test-first, not build-then-test**, for P3.T3–T5 (the hold-strategy
  implementations) and the race tests in P3.T7 — write the test capturing
  the correctness contract (exactly one winner under concurrent access)
  *before* the implementation. This is the one bug class in the whole
  project that silently corrupts the core guarantee if it's wrong.
- A **dedicated adversarial `/code-review` pass** runs at this phase's
  CHECKPOINT, on top of the routine `/pre-pr` gate and self-verification —
  not instead of them.

---

## P3.T1 — Scaffold Booking Service + `booking_db` schema/migrations

**Prompt to Claude Code:**
> Scaffold `booking-service` from the `event-service` template (app factory,
> `core.py`, `api/`/`db/`/`logic/`/`kafka/` layering, `/healthz`, `/metrics`,
> structured logging, Traefik routing with a specific `PathPrefix('/bookings')`
> — per the per-service layering and Traefik-routing conventions). Own
> Postgres database `booking_db` (§8) — no shared tables with `event_db`,
> cross-service data only arrives via Kafka. Define the schema and Alembic
> migration for: `Ticket` (one row per seat per event — section/row/label,
> status, `event_id`), `Booking` (organizer/user subject, status, timestamps
> per the lifecycle in §21), and whatever hold-state columns/table the chosen
> strategy interface (P3.T3) needs. **Unique constraint on `(event_id, seat
> identity)`** on `Ticket` — this is the idempotency mechanism §7 requires
> for provisioning (duplicate "event published" message must not create
> duplicate Ticket rows).

**Done when:** migrations run clean against a fresh `booking_db`; the unique
constraint is enforced (a manual duplicate insert attempt fails). Report
evidence: Booking Service class diagram + textual schema (feeds Class
Diagrams and Database Schema Design chapters).

---

## P3.T2 — Provisioning consumer (Kafka integration point #2)

**Prompt to Claude Code:**
> Add an `aiokafka` consumer (`kafka/consumers.py`) subscribed to Event
> Service's events topic. On an `upserted` message for a **published** event,
> upsert one `Ticket` row per seat from the message's event-carried seat list
> (§7.2 — no callback into Event Service's API). Idempotent by construction
> via P3.T1's unique constraint: a redelivered or duplicate message must not
> create duplicate rows or error — catch the constraint violation (or
> upsert-by-key) and treat it as a safe no-op, per §7's general
> idempotent-consumer rule. A `deleted` message should not need to touch
> `Ticket` rows once bookings exist against them (out of scope for this
> task — deletion-with-live-bookings is Event Service's `_check_no_bookings`
> stub, which this phase is what finally makes real; see P3.T6).

**Done when:** publishing an event through the real Event Service produces
the expected `Ticket` rows in `booking_db`; a redelivery test proves
duplicate messages change nothing. Report evidence: integration-point #2
sequence diagram.

---

## P3.T3 — `TicketHoldStrategy` interface + config switch

**Prompt to Claude Code:**
> Define the `TicketHoldStrategy` interface (§6) that both hold
> implementations (P3.T4, P3.T5) will satisfy — acquire a hold on a specific
> `Ticket`, release a hold, check hold status. Wire a config flag so the
> active strategy is selectable at startup (env var, not a runtime switch —
> this is what the Phase 8 benchmark harness toggles between runs). No
> business logic beyond the interface and the config wiring in this task —
> P3.T4/T5 provide the two bodies.
>
> **Test-first**: before writing the interface, write the test(s) that
> capture what "correct" means for *any* implementation of it — the
> contract, not a specific strategy's internals.

**Done when:** the interface exists, is documented, and a trivial
fake/no-op implementation can be swapped in via config for testing other
layers without either real strategy running. Report evidence: strategy-
pattern writeup (why an interface here, not a second Manager-as-router — see
`CLAUDE.md`'s "Internal per-service layering" section for the reasoning
already established for this project).

---

## P3.T4 — Cron hold strategy

**Prompt to Claude Code:**
> Implement the cron-based `TicketHoldStrategy`: acquiring a hold sets the
> `Ticket`'s status to held plus an expiry timestamp; a periodic APScheduler
> sweep finds expired holds and releases them back to available. **Test-first**
> per this phase's process note — write the concurrency-correctness test
> (two clients race one seat, exactly one acquires the hold) before the
> implementation.

**Done when:** the abandoned-hold-auto-releases-on-sweep test passes; the
race test from above passes under this strategy specifically.

---

## P3.T5 — Redis TTL hold strategy

**Prompt to Claude Code:**
> Implement the Redis-backed `TicketHoldStrategy`: acquiring a hold is a
> `SET key value NX EX seconds` against Redis (atomic acquire-or-fail,
> auto-expiring — no sweep process needed, unlike P3.T4). Releasing a hold
> deletes the key. **Test-first**, same as P3.T4 — the race test before the
> implementation, run against this strategy.

**Done when:** the abandoned-hold-auto-releases-on-TTL test passes (no sweep
needed — verify by asserting the key is simply gone after TTL, not via a
scheduler run); the same race test from P3.T4 passes under this strategy
too, proving both satisfy the same contract.

---

## P3.T6 — Booking flow API

**Prompt to Claude Code:**
> Add the booking flow endpoint(s): acquire a hold on a specific seat via the
> configured `TicketHoldStrategy`, create a `Booking` row in **`PENDING`**
> status (§21 — before payment, not after), return the booking ID. No-
> double-booking is enforced intra-service by the hold strategy itself (§8 —
> the whole reason `booking_db` is this service's alone). Explicit auth:
> authenticated user only, no organizer role required (any logged-in user
> books a ticket) — state this decision in the route, don't leave it
> implicit, per the auth-requirement convention. This is also where Event
> Service's `_check_no_bookings` stub (currently always `return`s — see
> `event_manager.py`) needs a real counterpart on this side: decide and
> document how an organizer's delete-event request should behave once real
> `Ticket`/`Booking` rows exist for that event.

**Done when:** the happy path returns a `PENDING` booking with a held seat;
attempting to book an already-held/booked seat fails cleanly. Report
evidence: booking lifecycle state diagram (§21).

---

## P3.T7 — Concurrency tests (testcontainers Postgres + Redis + Kafka)

**Prompt to Claude Code:**
> Write the full concurrency test suite this phase's correctness claim rests
> on: N clients race one seat, under **each** hold strategy, exactly one
> wins — the two tests already written test-first in P3.T4/T5, now run
> together against real Postgres + real Redis + real Kafka via
> `testcontainers-python`, not mocks. Also cover: duplicate provisioning
> message creates no duplicate tickets (P3.T2's idempotency claim, proven
> here rather than just asserted); an abandoned hold releases under both
> strategies (cron sweep and Redis TTL) within a bounded wait.

**Done when:** full suite green under both strategies. Report evidence: this
is the foundation the Feature Development Process chapter's benchmark
(Phase 8) builds on — the correctness proof that has to hold before the
performance numbers mean anything.

---

## Phase 3 exit checklist (all must pass before P8)

- [ ] Booking Service scaffolded, `booking_db` migrated, unique constraint on
      `(event_id, seat)` enforced.
- [ ] Provisioning consumer idempotent; redelivery proven a no-op.
- [ ] `TicketHoldStrategy` interface with both implementations passing the
      same concurrency-correctness contract.
- [ ] Booking flow API: happy path + no-double-booking, both proven live.
- [ ] Full concurrency test suite green under both strategies
      (testcontainers Postgres + Redis + Kafka).
- [ ] Validation checkpoint done live: two concurrent requests for the same
      seat under each strategy → exactly one `PENDING` booking; duplicate
      provisioning message → no duplicate tickets; abandoned hold releases
      under both strategies.
- [ ] Dedicated adversarial `/code-review` pass run on top of the routine
      `/pre-pr` gate and self-verification (per this phase's process note
      above) — not a substitute for either.
- [ ] Live walkthrough done at CHECKPOINT; `docs/architecture.html` updated
      to current state; `docs/build-log.md` entry appended; decisions-log
      delta logged if any (e.g. if the `_check_no_bookings` resolution from
      P3.T6 changes the deferred behavior §-referenced in Phase 1).
- [ ] Phase-end checklist item 7 (`/pre-pr`) run against the diff since this
      phase's starting commit, findings self-applied.

**Report evidence captured this phase (§16):** Booking class diagram +
textual schema, integration-point #2 sequence diagram, strategy-pattern
writeup, booking lifecycle state diagram, concurrency test suite as the
Feature Development Process chapter's correctness foundation.

**Next:** Phase 8 — Hold-Mechanism Benchmark (report centerpiece, pulled
forward ahead of Payment Service per the locked report-first build order).
Runs directly against this phase's two hold strategies with real
concurrent-client load. Generate its task prompts once Phase 3's exit
checklist is green.
