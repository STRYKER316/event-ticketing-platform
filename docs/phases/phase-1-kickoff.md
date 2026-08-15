# Phase 1 Kickoff — Event Service

**Goal of this phase:** the service everything else depends on. Events, venues, and
performers live in Postgres (`event_db`); seat-map layouts live in MongoDB. Organizer
writes are ownership-scoped (§15) — role checks alone are not enough.

**How to use this file:** run the seven tasks below **in order**, one per Claude Code
session. Commit after each (small, green commits, per the per-service layering
conventions in `CLAUDE.md`). `main` stays bootable at every step. Give Claude Code the
repo plus `decisions-log.md` and `master-development-plan.md` as context so it stays
anchored to the locked decisions (referenced by § below).

**Entry deps:** Phase 0 (walking skeleton) — verified complete; see `docs/build-log.md`
and the Phase 0 exit checklist.

---

## P1.T1 — Scaffold Event Service from the P0.T5 template
**Prompt to Claude Code:**
> Extend the existing `event-service` (already scaffolded in P0.T5 with the app
> factory, `core.py`, `api/`/`db/`/`logic/` layering, `/healthz`, `/metrics`, structured
> logging) so it's ready for real feature work: confirm its async SQLAlchemy engine
> points at `event_db`, add a Motor client in `core.py` for MongoDB (a separate
> logical database/collection namespace for this service, per database-per-service),
> and confirm its Traefik routing + compose entry are correct. Remove the throwaway
> `api/demo.py` route now that real endpoints are coming. No new business endpoints
> yet — this task is wiring only.

**Done when:** service is healthy behind Traefik with both a working Postgres
connection (existing `/healthz` DB check) and a working Mongo connection.

---

## P1.T2 — Postgres schema + Alembic migrations
**Prompt to Claude Code:**
> Design the `event_db` schema for events, venues, and performers as async SQLAlchemy
> models under `app/db/models.py`: `Venue` (name, address, capacity), `Performer`
> (name, bio), `Event` (title, description, start/end time, venue FK, organizer ID
> from the JWT subject, status), and an `Event`↔`Performer` association (many-to-many
> — an event can have multiple performers). Set up Alembic for this service (if not
> already done as part of the shared service template) and write the initial
> migration. Write repository methods for basic CRUD, following the Repository layer
> convention (query/write only, `flush()` not `commit()`, no business rules).

**Done when:** `alembic upgrade head` runs clean against `event_db`; models are
CRUD-able via a quick script or test. Report evidence: ER diagram + textual schema.

---

## P1.T3 — MongoDB seat-map documents
**Prompt to Claude Code:**
> Design the seat-map document shape in MongoDB (via Motor): one document per event,
> containing sections, each with rows, each row with an ordered list of seats (seat
> label/number, coordinates or ordering info, section/row it belongs to). This is
> reserved/numbered seating only (no GA) per the architecture invariants. Add a
> repository method to store and fetch a seat-map document by event ID, following the
> same Repository-layer discipline as the Postgres side (no business rules in the
> repository).

**Done when:** a seat-map document can be stored and fetched by event ID. Report
evidence: sample seat-map JSON figure.

---

## P1.T4 — Read APIs
**Prompt to Claude Code:**
> Add public read endpoints via an `EventManager` (or split `EventManager`/
> `VenueManager` if that reads cleaner — your call, stay consistent with the
> Manager+Repository convention either way): `GET /events` (list, paginated and
> sortable — e.g. by start time), `GET /events/{id}` (detail, including its
> performers and venue), `GET /venues/{id}` (venue detail), and `GET
> /events/{id}/seat-map` (fetch the Mongo seat-map document for that event). These
> are public routes — no auth dependency — per the "every endpoint's auth requirement
> is explicit" convention, mark that decision in the route definitions/docstrings,
> don't leave it implicit by omission.

**Done when:** endpoints return seeded data correctly; pagination and sorting work
against a realistic multi-row dataset. Report evidence: Event Service class diagram.

---

## P1.T5 — Organizer write APIs with ownership scoping
**Prompt to Claude Code:**
> Add organizer-only write endpoints: `POST /events` (create, sets `organizer_id`
> from the JWT subject), `PATCH /events/{id}` (update), `DELETE /events/{id}`
> (delete-only-if-zero-bookings — for now, since Booking Service doesn't exist until
> Phase 3, this can be a no-op check that always allows deletion, but structure the
> Manager method with the guard clause already in place and a comment-free TODO-free
> note in the phase doc, not the code, that P3 wires the real booking check). Use the
> shared `require_role("organizer")` dependency plus an explicit ownership check
> inside the Manager (compare the resource's `organizer_id` against the JWT subject,
> per §15) — role alone is not sufficient. Write the DTOs in `api/schemas.py` per the
> DTO conventions (real enums not bare `str`, constrained non-blank strings, validators
> for date/logical constraints like start time not in the past).

**Done when:** a `user`-role token gets 403 on all three routes; an organizer editing
another organizer's event gets 403; the owning organizer succeeds end to end. Report
evidence: roles/permissions table.

---

## P1.T6 — Tests
**Prompt to Claude Code:**
> Write the test suite for this phase's feature work (build-then-test, per
> `CLAUDE.md` — this phase isn't in the test-first list). Unit tests: role
> enforcement and ownership-scoping logic in the write-path Managers (mock or
> lightweight session). Integration tests via `testcontainers-python`: real Postgres
> + real MongoDB, covering the full create → fetch event → fetch seat-map flow, and
> the 403 cases from P1.T5 against a real DB-backed ownership check.

**Done when:** full suite green (`pytest`, unit + integration). Report evidence:
testing-chapter material (what's unit- vs. integration-covered and why).

---

## P1.T7 — Seed script
**Prompt to Claude Code:**
> Write a seed script (per decisions-log §19) that populates baseline demo state:
> a handful of venues, performers, events (spanning past/future, multiple
> organizers), and matching seat-map documents for at least one event so downstream
> phases (Search, Booking) have realistic data to build against. Wire it into
> `make seed` (or the equivalent Makefile/justfile target from P0.T7).

**Done when:** `make seed` populates the demo state from a clean stack.

---

## Phase 1 exit checklist (all must pass before P2)

- [ ] Event Service healthy behind Traefik, both Postgres and Mongo connected.
- [ ] `alembic upgrade head` clean; ER diagram captured.
- [ ] Seat-map documents storable/fetchable by event ID.
- [ ] Read APIs return seeded data with working pagination/sorting.
- [ ] Organizer write APIs enforce both role **and** ownership scoping.
- [ ] Full unit + integration test suite green.
- [ ] `make seed` works from a clean checkout.
- [ ] Live walkthrough done at CHECKPOINT (per `CLAUDE.md` cadence); `docs/architecture.html`
      updated to current state; `docs/build-log.md` entry appended; decisions-log
      delta logged if any.

**Report evidence captured this phase (§16):** ER diagram + textual schema, sample
seat-map JSON figure, Event Service class diagram, roles/permissions table, testing-
chapter material.

**Next:** Phase 2 — Search Service + Kafka #1 (browse/search vertical slice). Generate
its task prompts the same way once Phase 1's exit checklist is green.
