# Phase 2 Kickoff — Search Service + Kafka #1

**Goal of this phase:** the first real cross-service Kafka integration — Event Service
publishes on create/update/delete, Search Service consumes into Elasticsearch. This
derisks event-carried state transfer (§7.2) and idempotent-consumer discipline (§7)
early, before Booking Service (Phase 3) depends on the same patterns for provisioning.
End-to-end browse→search becomes demoable.

**How to use this file:** run the five tasks below **in order**, one per Claude Code
session. Commit after each (small, green commits, per the per-service layering
conventions in `CLAUDE.md`). `main` stays bootable at every step. Give Claude Code the
repo plus `decisions-log.md` and `master-development-plan.md` as context so it stays
anchored to the locked decisions (referenced by § below).

**Entry deps:** Phase 1 (Event Service) — complete; see `docs/build-log.md` and the
Phase 1 exit checklist. Kafka (KRaft mode, no Zookeeper) already runs in
`infra/docker-compose.yml`; no service yet produces or consumes on it.

---

## P2.T1 — Event Service Kafka producer
**Prompt to Claude Code:**
> Add an `aiokafka` producer to Event Service, following the per-service layering
> convention (`kafka/producers.py`). On event create, update, and delete, publish the
> full event payload to the events topic — **event-carried state transfer** per §7.2:
> include the full seat list (section/row/number) from the venue's seat map, not just
> a venue ID, so Search Service (and later Booking Service in P3) never needs a
> synchronous callback into Event Service's API. Key each message for idempotency
> (event ID) so redelivery/compaction behaves correctly downstream. Call the producer
> from `EventManager`'s existing create/update/delete methods, after the Postgres
> commit — same "commit first, side-effect after" ordering already accepted for the
> Mongo seat-map delete in Phase 1 (documented residual risk, not a new one). Wire
> producer startup/shutdown into `main.py`'s lifespan alongside the existing Postgres/
> Mongo connections.

**Done when:** creating, updating, and deleting an event produces exactly one message
per mutation, observable on the topic (e.g. via `kafka-console-consumer` or a quick
test consumer). Report evidence: integration-point #1 sequence diagram.

---

## P2.T2 — Search Service scaffold + Elasticsearch client + index mapping
**Prompt to Claude Code:**
> Scaffold `search-service` from the `event-service` template (app factory, `core.py`,
> `api/`/`db/`/`logic`/`kafka/` layering, `/healthz`, `/metrics`, structured logging) —
> per the "new service = copy the template" convention. This service has no Postgres/
> Mongo of its own (§8 — Elasticsearch is not a source of truth); `core.py` instead
> holds an `elasticsearch-py` async client. Define the index mapping for events:
> searchable/filterable fields (title, description, start/end time, venue, performers,
> status) plus the seat list carried in from the Kafka payload. Create the index on
> service boot if it doesn't already exist. Add Traefik routing + compose entry.

**Done when:** service is healthy behind Traefik; the ES index exists after a clean
`docker compose up`.

---

## P2.T3 — Search Service Kafka consumer (idempotent)
**Prompt to Claude Code:**
> Add an `aiokafka` consumer (`kafka/consumers.py`) that upserts or deletes the
> corresponding Elasticsearch document per Event Service's messages from P2.T1. Use
> the event ID as the ES document ID so a redelivered "upsert" message is naturally
> idempotent (an upsert-by-ID is a safe no-op, not a duplicate document) — per §7's
> general idempotent-consumer rule, the same reasoning Phase 3's ticket provisioning
> will need. A delete message removes the document by ID; deleting an already-deleted
> ID is also a safe no-op. Route the handler through a thin construct — no
> business-logic Manager needed here since ES upsert/delete *is* the operation, not a
> multi-step sequence — but keep the consumer handler itself thin (parse → dispatch),
> not a dumping ground for ES-client calls inline.

**Done when:** a redelivery test proves duplicate messages produce no duplicate or
inconsistent documents (same doc, same content, no error). Report evidence:
idempotency note (what makes this consumer safe under at-least-once delivery).

---

## P2.T4 — Search API
**Prompt to Claude Code:**
> Add a public (no auth — mark that explicitly per the auth-requirement convention)
> `GET /search` endpoint: free-text query across title/description/venue/performers,
> paginated and sortable (e.g. by relevance or start time), backed by the ES client.
> Follow the Manager+Repository split even though there's no SQL/Mongo underneath —
> a thin `SearchManager` wrapping an ES query-building/repository-style module keeps
> the same layering discipline as every other service, and keeps the ES query DSL out
> of the route.

**Done when:** search returns indexed events with working pagination and sorting
against a realistic result set (seeded via Phase 1's `make seed` data, once it's
flowed through Kafka). Report evidence: search feature writeup.

---

## P2.T5 — Integration test (testcontainers ES + Kafka)
**Prompt to Claude Code:**
> Write the test suite for this phase's feature work (build-then-test, per
> `CLAUDE.md` — this phase isn't in the test-first list). Integration test via
> `testcontainers-python`: real Kafka + real Elasticsearch, covering publish event →
> Search Service consumes → document becomes queryable. Explicitly capture and assert
> around the eventual-consistency window (§7) — the test should show the document
> isn't there immediately at publish time but is there after the consumer processes
> the message, not paper over the gap. Add the redelivery-is-a-no-op test for the
> consumer (duplicate message, assert no duplicate/changed document) alongside it.
> Unit tests: producer message-building (payload shape, correct key) and any pure
> query-building logic in `SearchManager`.

**Done when:** full suite green (`pytest`, unit + integration). Report evidence:
eventual-consistency talking point (§26) with the test as its concrete backing.

---

## Phase 2 exit checklist (all must pass before P3)

- [x] Event Service publishes exactly one Kafka message per create/update/delete,
      keyed for idempotency, carrying full event-carried state (seat list included).
      (Amended: publish/update-while-published/delete, not raw create — see §15 delta.)
- [x] Search Service healthy behind Traefik; ES index created on boot.
- [x] Search Service consumer upserts/deletes ES docs; redelivery is a proven no-op.
- [x] `GET /search` returns indexed events with working pagination/sorting.
- [x] Full unit + integration test suite green, including the eventual-consistency
      and redelivery tests.
- [x] Validation checkpoint done live: create an event → appears in search within the
      consistency window; delete → disappears; duplicate Kafka delivery changes
      nothing.
- [x] Live walkthrough done at CHECKPOINT (per `CLAUDE.md` cadence);
      `docs/architecture.html` updated to current state; `docs/build-log.md` entry
      appended; decisions-log delta logged if any.
- [x] Phase-end checklist item 7 (`/pre-pr` — simplify → code-review → verify) run
      against the diff since Phase 1's checkpoint commit, findings self-applied.

**Report evidence captured this phase (§16):** integration-point #1 sequence diagram,
idempotency note, search feature writeup, eventual-consistency talking point (§26).

**Next:** Phase 3 — Booking Service + Provisioning + Dual Hold (the heart of the
system). Generate its task prompts the same way once Phase 2's exit checklist is
green. Phase 3's provisioning consumer (Kafka #2) reuses the exact idempotent-consumer
pattern established here.
