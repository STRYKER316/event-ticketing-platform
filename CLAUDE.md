# Scaler_Neovarsity_MS_CS_Capstone

## What this is

Solo backend capstone (Scaler-Neovarsity x Woolf, MS CS, Backend Specialization) — an
event ticketing/booking platform in the BookMyShow/Ticketmaster mold. Microservices,
FastAPI, Kafka-driven integration, Postgres/MongoDB/Elasticsearch/Redis, Keycloak auth,
Stripe (test mode), Docker + AWS Elastic Beanstalk. This is a graded academic
deliverable with an accompanying 40-page report — see "Integrity rule" below before
touching status labels or benchmark numbers.

**Read `/docs/decisions-log.md` and `/docs/master-development-plan.md` before making
any architectural or scope decision.** They are locked (§1–27) and this file does not
override them — it's a quick-reference summary so you don't have to re-read both in
full every session. When in doubt, the decisions log wins.

## Repo layout (monorepo, decisions-log §20)

```
/services
  /event-service       Postgres (event_db) + MongoDB seat maps; organizer writes
  /search-service       Elasticsearch index; Kafka consumer only, not source of truth
  /booking-service      Postgres (booking_db); dual hold strategy; the heart of the system
  /payment-service      Postgres (payment_db); Stripe test mode
  /notification-service no DB (or minimal delivery log); hand-rolled retry/DLQ
  /_shared/auth          shared FastAPI JWT-validation dependency (build once in P0, reuse everywhere)
/frontend                minimal React, 5 screens, Nginx-served (Phase 7, not before)
/infra                   docker-compose.yml, Traefik config, Keycloak realm export
/benchmark               standalone P8 load harness (own pyproject.toml/uv venv, not
                         part of the /services workspace — mirrors infra/kafka-smoke-test)
/docs                    decisions-log.md, master-development-plan.md,
                         build-log.md (append-only build diary — decisions, failures, fixes),
                         architecture.html (living topology + flow diagrams, current state —
                         see "End-of-phase walkthrough" below)
  /phases                 phase-0-kickoff.md, phase-1-kickoff.md, ... — one file per phase,
                           generated from master-development-plan.md as each phase starts
  /report                 continuously-drafted report chapters (§16 evidence map) — README.md
                           has the chapter status table; source material for the P11 template
                           assembly, not the final formatted document itself
```

## Non-negotiable architecture invariants

These hold across every service, every phase — check new code against them:

- **Database-per-service.** No service queries another service's tables directly, ever.
  Cross-service data only arrives via Kafka events — **with one narrow, deliberate
  exception** (decisions-log §9 amendment, Phase 4): Booking Service makes a synchronous
  HTTP call to Payment Service to initiate a charge (`POST /bookings/{id}/pay` →
  `POST /payments/charge`), forwarding the caller's JWT. This is not a Kafka integration
  point and does not query another service's tables — it's a request/response action
  needing an immediate result, which Kafka's fire-and-forget shape doesn't fit. It is the
  only synchronous inter-service call in the system; don't add a second one without the
  same kind of explicit discussion the first one got.
- **Five Kafka integration points only** (decisions-log §7) — event↔search, event→booking
  provisioning, booking/payment→notification, payment→booking (outcome: succeeded→confirm,
  failed→release), booking→payment (cancelled→refund). Don't invent a sixth without
  discussing it first — point #4 was broadened to carry both outcomes on one topic
  specifically to avoid growing to six (§7 amendment, Phase 4).
- **Every Kafka consumer is idempotent.** Redelivery must be a safe no-op — this gets
  explicitly tested (unique constraints, "only transition if currently in state X," etc.),
  not assumed.
- **Ownership scoping, not just role checks.** `organizer` role is necessary but not
  sufficient — always compare the resource's owning-organizer ID against the JWT subject.
- **No distributed transactions.** Compensation (refunds, hold release) reuses existing
  release/notify paths rather than inventing saga rollback machinery.
- **Reserved/numbered seating only** — every Ticket row is one specific seat for one event.

## Tech stack

FastAPI (async), SQLAlchemy async + asyncpg + Alembic, Motor (MongoDB), aiokafka
(KRaft-mode Kafka, no Zookeeper), redis.asyncio, APScheduler, elasticsearch-py,
stripe-python, structlog (JSON logs), prometheus-fastapi-instrumentator, Traefik v3
(gateway), Keycloak (dev mode, JWT/OIDC), pytest + testcontainers-python, a
standalone Python `asyncio` load harness (`/benchmark`, resolved over k6 in P8.T2 —
reuses the `asyncio.gather` concurrent-client pattern already proven in P3.T7),
React (frontend, Phase 7 only). `uv` for Python
dependency/workspace management (one shared venv/lockfile across `/services`, §
"Restructure: uv workspace" in build-log.md; `/benchmark` and
`infra/kafka-smoke-test` are standalone `uv` tools outside that shared workspace).

## Workflow & cadence (decisions-log §25, §27)

- **Local-first.** Everything is built and tested against `docker compose` locally.
  AWS (Elastic Beanstalk) is a late-phase deployment target, not a dev environment.
- **Per-phase loop:** PLAN → REVIEW PLAN → IMPLEMENT (small, continuous, green commits —
  don't batch a whole phase into one commit) → TEST (pytest unit + testcontainers
  integration; Kafka phases must test redelivery-is-a-no-op — see "Test-first vs.
  build-then-test" below for which comes first) → REVIEW (self-verification via live
  testing on every phase per the Integrity rule; additionally run a dedicated
  `/code-review` pass — not just self-verification — on P3 and P8 specifically, see
  below) → DOCUMENT
  (draft the report section this phase feeds into `docs/report/` — see its README
  for the chapter map and status table, update both the chapter file and that table
  every phase; log any decisions-log delta; also append an entry to
  `docs/build-log.md` — what was built, what failed and why, what fixed it, any ad
  hoc decision too small for the decisions log) →
  CHECKPOINT (tag/merge at the phase boundary).
- **A `docs/phases/phase-N-kickoff.md` that already exists for the phase being
  started IS the completed PLAN step — do not re-plan it.** When the user kicks off
  a phase this file already covers, don't invoke `superpowers:brainstorming` or
  `superpowers:writing-plans` — the kickoff doc's per-task prompts, done-when
  criteria, and exit checklist already are the plan those skills would otherwise
  produce, and running them again burns session budget re-deriving something
  already on disk (this happened starting Phase 3: ~10-12% of session usage spent
  on a redundant brainstorming pass before the existing kickoff doc was even read).
  Read the kickoff doc, then go straight to IMPLEMENT using its task list —
  `superpowers:executing-plans` or `superpowers:subagent-driven-development` are
  fine for *execution structure* on that already-written plan, but the plan itself
  doesn't need re-generating. Only fall back to actual brainstorming/planning if no
  kickoff doc exists yet for the phase being started, or the user explicitly asks
  to revise the plan.
- **Test-first vs. build-then-test — decided per task, not blanket.** Write the test
  capturing the correctness contract *before* the implementation for business-logic
  Manager methods on correctness-critical paths specifically: the dual hold strategies
  (P3 — this is the double-booking-critical path the whole benchmark chapter depends
  on) and payment/webhook idempotency (P4). Everywhere else — routes, schemas,
  scaffolding, infra wiring, most CRUD Manager methods — build then test, matching how
  `master-development-plan.md` already sequences a dedicated Tests task at the end of
  each phase's task list; don't fight that structure by forcing TDD onto tasks it
  wasn't planned around. When in doubt: if a task is "does this behave correctly under
  a tricky concurrent/edge case," lean test-first; if it's "does this plumbing work,"
  build-then-test is fine.
- **Dedicated code-review pass on P3 and P8 only, not every task.** Self-verification
  (live testing before claiming done) is the default and matches the Integrity rule —
  fine for routine work. P3 (dual seat-hold, the one bug class that silently corrupts
  the product's core guarantee) and P8 (the benchmark — its numbers are the report's
  only Measured chapter, errors here are load-bearing for the whole report) get an
  additional adversarial `/code-review` pass before CHECKPOINT, on top of
  self-verification, not instead of it.
- **End-of-phase walkthrough.** At CHECKPOINT — not after every task — do a live,
  narrated walkthrough with the user: bring the stack up, hit real endpoints, show
  real logs/output, don't just describe it. Then update `docs/architecture.html` to
  match current state (topology + key flow diagrams, current-state focus like
  `git status` not `git log` — it shows what's true *now*, `build-log.md` is where
  history lives). Same file every phase, redeployed in place, not a new file per
  phase. Commit it as part of the phase's checkpoint. This is also the report's
  §16 topology/flow evidence, produced once, not redone later for the report.
- **Phase-end checklist.** Before tagging CHECKPOINT, run through all nine — most are
  already required by the bullets above, this is the standing list so none get
  skipped by accident:
  1. **End-to-end testing** — live walkthrough against the real stack (see
     "End-of-phase walkthrough" above), not just the unit/integration suite passing.
  2. **Report writing** — the phase's `docs/report/` chapter(s) drafted/updated, and
     the chapter-status table in `docs/report/README.md` updated to match.
  3. **Commit history & file scanning for rule deviations** — this phase's commits
     checked against the commit-granularity rule below, and any new/changed files
     checked against the Academic-presentation hard rules (no emoji, no casual
     language, no stray TODOs, etc.).
  4. **`docs/architecture.html` updated** to current state — see "End-of-phase
     walkthrough" above; this is a distinct deliverable from the report chapters,
     easy to forget since nothing else forces it.
  5. **`decisions-log.md` delta check** — explicitly ask "did anything decided this
     phase change or extend a locked decision," don't just assume no. Distinct from
     `build-log.md` (the diary of what happened) — decisions-log is the normative
     record.
  6. **`CLAUDE.md` self-update check** — it says to update itself when conventions,
     commands, or structure shift; explicitly check rather than only checking new
     code against what's already written here.
  7. **Review gate on the phase's accumulated diff** — no branch/PR workflow exists
     here (commits land straight on `main`, deliberately, per the commit-granularity
     rule below), so there's no PR to raise; instead run `/pre-pr` (simplify →
     code-review → verify, each as a subagent) against the diff between `main` and
     the commit the phase started from, and self-apply anything real, before the
     CHECKPOINT commit. This is the routine-work review pass; it's in addition to,
     not instead of, the dedicated adversarial `/code-review` pass P3 and P8 get on
     top of it for their higher-stakes correctness paths.
  8. **Cross-doc staleness sweep** — list what this phase's diff actually changed
     (renamed/added method signatures, new class dependencies, removed routes, an
     amended decision), then grep `docs/` and `infra/` for other places describing
     those same symbols or behaviors and verify each is still accurate. This is what
     catches a report chapter documenting a now-stale method signature, an
     `infra/README.md` that never learned a new service exists, or a
     `master-development-plan.md` task-table row a later decisions-log amendment
     quietly outdated but never came back to fix — none of which item 4
     (`architecture.html`) or item 5 (decisions-log delta) catches, since both only
     look at what this phase *produced*, not what it *invalidated*. Not a full-repo
     re-read every phase — scoped to what the diff actually touched. Applies to any
     checkpoint-worthy session, not just a numbered phase — an addendum like the P1
     seat-map gap-closing session needs it too. Goal: every phase doc, report chapter,
     and other current-state doc stays in sync with the code, not just
     `architecture.html`.
  9. **Check off this phase's own `docs/phases/phase-N-kickoff.md` exit checklist** —
     every `[ ]` under "Phase N exit checklist" flipped to `[x]`, each with a one-line
     note pointing at the actual evidence (a test count, a live-verified behavior, a
     decisions-log delta), not just the box ticked blind. Added after Phase 3's own
     kickoff doc sat with every exit-checklist item still unchecked despite the phase
     being genuinely, verifiably done — items 1-8 above all check the *work*, but
     nothing checked whether the *tracking doc for the work* itself got updated, so it
     silently drifted every phase until someone asked "does this look done to you?"
     and the unchecked boxes were the tell. `phase-0/1/2-kickoff.md` got this right at
     the time; Phase 3 didn't, and this item exists so a future phase can't repeat it.

  Lower-priority, worth doing before a real external-facing moment (public repo, demo,
  submission) rather than every phase: verify `main` boots from a genuinely clean
  checkout (fresh clone), not just `make down && make up` against an
  already-migrated/seeded volume.
- **Before-push checklist.** Distinct from the phase-end checklist above and run on
  its own cadence — commits land on `main` continuously (many per phase), but pushing
  to `origin` is a separate, less frequent, and less reversible act (the first point
  anything becomes visible outside this machine), so it gets its own gate rather than
  being folded into CHECKPOINT:
  1. **Working tree clean** — nothing uncommitted or stray left behind by mistake.
  2. **Secret/credential scan across exactly the commits about to be pushed** —
     `git log -p origin/main..HEAD` (or equivalent) checked for anything that
     shouldn't be there, not just trusting `.gitignore`.
  3. **Fast-forward check** — confirm `origin/main` hasn't diverged, so this is a
     plain fast-forward, not a surprise merge/force-push situation.
  4. **Academic-presentation full scan** — the Academic-presentation hard rules
     section below already calls for re-running the full scan (commit history,
     tracked-file content, secrets, LICENSE, tone) "before any major external-facing
     moment"; a push to `origin` **is** that moment, concretely, not just whenever it
     feels major — this is where that scan actually fires.
  Pushing itself still always gets a confirmation from the user regardless of how
  clean this checklist comes back — that's a standing rule, not specific to this repo.
- **`main` stays bootable at every commit.** If a session ends mid-task, the previous
  commit should still `docker compose up` cleanly. Config-gate anything half-finished
  rather than leaving `main` broken.
- **Build order is locked (report-first):** P0 → P1 → P2 → P3 → **P8 (benchmark)** →
  P4 → P6 → P5 → P7 → P10 → P9/P11. The benchmark runs right after Booking, before
  Payment — it's the report's only Measured chapter. Don't reorder this without
  updating the master plan.
- Each phase's task-by-task prompts live in `/docs/phases/phase-N-kickoff.md` (Phase 0's
  is there already); later phases get the same treatment generated from `master-development-plan.md`
  section-by-section as they come up.

## Integrity rule — read before writing status anywhere

Status labels are **Planned → Implemented → Tested → Deployed → Measured → Verified**.
Never describe something as done, tested, or working unless it actually reached that
label through real execution in this repo. This applies to code comments, commit
messages, README claims, and especially anything destined for the report — the
Hold-Mechanism Benchmark (Phase 8) numbers in particular must never be fabricated,
estimated, or placeholder values that could leak into the report undetected.

## Academic-presentation hard rules — non-negotiable

This repo is graded MS CS capstone work. Everything in it — commit history, file
contents, structure — should read as such to anyone browsing it, not just the
report PDF. Established 2026-08-14 after a full repo/commit-history scan found no
secrets/credential leaks and a clean `.gitignore`, but did find casual-reading
issues worth locking in against:

- **No emoji anywhere in committed files** — code, docs, commit messages. Use
  plain text (`**Verified.**`, `- [x]`) for status markers, not `✅`/`🎉`/etc.
  Emoji in my own conversational replies to the user is fine; emoji in anything
  that gets committed is not.
- **No LICENSE file** while this is active graded coursework under review —
  defaults to all-rights-reserved, avoids ambiguity during grading. Revisit only
  if there's an explicit later decision to open-source it post-submission.
- **AI-tool use stays fully visible, on purpose** — "Claude Code" is named
  throughout `/docs` because these are literally instructions written for it to
  execute, and that transparency is a deliberate integrity stance for this
  project (consistent with `docs/build-log.md`'s whole premise), not an oversight
  to quietly clean up.
- **No casual/unprofessional language** in anything committed — no profanity, no
  "hacky"/"dumb"/"lol"-register commentary, no stray `TODO`/`FIXME`/`XXX` left
  sitting in committed code (track real follow-ups in the phase kickoff docs or
  build-log instead, not code comments that read as unfinished work).
- **Commit author identity:** currently `STRYKER316 <ansil.mishra316@gmail.com>`
  on most commits (a handle, not a name) — explicitly left as-is per the user's
  choice on 2026-08-14 when this was flagged. Don't "fix" this unprompted; if it
  changes, it'll be a deliberate ask, not an assumption on my part.
- Re-run a full scan like this one (`git log`, tracked-file content, secrets,
  large/binary files, LICENSE, tone) before any major external-facing moment —
  making the repo public, a demo, or submission — not just once at Phase 0.

## Conventions

- **Keep every folder scannable at a glance — group into subfolders before a flat
  listing turns into a pile.** If a folder is about to hold more than ~6–8 files of
  the same kind (one per phase, one per component, one per migration, etc.), give
  them a subfolder up front rather than after the tenth one lands — see
  `docs/phases/` as the pattern (moved there while it was one file, not eleven).
  This applies repo-wide, not just `/docs` — `/infra` already follows it
  (`keycloak/`, `postgres/`, `docker-socket-proxy/`, `kafka-smoke-test/` instead of
  everything loose at the top level); keep new infra pieces and any other
  fast-growing folder to the same standard.
- Python 3.12, async throughout — no sync `Session` or blocking calls in a request path.
  This is locked (§3): async I/O was a deciding factor over Spring Boot, so don't reach
  for sync patterns even if a past reference codebase does.
- **Commit messages: single imperative subject line, no body paragraph.** Never reference
  a phase/task ID inside the message itself (`P3.T4`, `(Phase 3)`) — that context lives in
  `master-development-plan.md`, not git history. e.g. `booking-service: add Redis TTL hold
  strategy`, not `booking-service: add Redis TTL hold strategy (P3.T5)`.
- **Every commit is one real, complete unit of work — never a bare mechanical
  tweak, never a whole session crammed into one commit.** "Incremental" means
  committing at the boundary of a real unit of work, not after every file edit
  and not once at the end of a long session either. A unit of work is: a code
  change, or a doc change substantial enough to stand alone in history — a new
  file, a real rewrite, a genuine reorg (`docs/phases/`, `architecture.html`).
  It is **not**: a one-line reference fix, a single deleted file, a short
  clarifying note, an emoji swap, a checklist tick, a build-log entry for the
  task just finished. None of those earn a commit by themselves. When one
  comes up, either fold it into whatever real commit is already happening, or
  — if several small fixes pile up in one session with nothing else to attach
  to — batch them together into *one* commit, not one each. Before committing,
  ask: "does this stand on its own in history, or is it commit-spam?" A
  docs-only change never rides silently inside a code commit's diff without
  being mentioned in the message, and a commit message never *sounds*
  doc-only when it isn't (avoid leading verbs like "Document," "Record,"
  "Confirm" on a commit that actually changes code) — but "gets its own
  commit" no longer means "gets its own commit every single time." (Locked
  2026-08-14 after several sessions produced exactly the commit-spam this
  rule now forbids — see `docs/build-log.md` for the squashes that fixed it.)
- **DTO layer is a strict validation boundary, not a formality.** Every enum-backed field
  is typed as its real Python `Enum`, never `str` — let Pydantic's automatic 422 reject
  bad values before business logic ever sees them. Reject blank/whitespace-only required
  strings via a constrained type, not bare `str`. Any field with a logical constraint
  beyond its raw type (a date that can't be in the future, an amount that can't be
  negative) gets a validator at the DTO layer, not a downstream check several calls deep.
- **A float field rejecting `NaN`/`Infinity` (`Field(allow_inf_nan=False)`) needs the
  app-level `RequestValidationError` handler from `event-service/app/main.py` copied
  too, not just the field constraint.** FastAPI's default handler echoes the rejected
  value back in the 422 body, and Starlette's JSON encoder can't serialize `NaN`, so
  the rejection itself 500s. Any service with a money/measurement float field (Payment
  Service's `amount`, most likely) needs this.
- **A `Settings` field whose valid values are a small fixed set of strings is typed
  `Literal[...]`, never bare `str`** — the same "reject bad values before they reach
  business logic" reasoning as the DTO-Enum rule above, just applied to config instead
  of a request body. `booking-service`'s `hold_strategy: Literal["cron", "redis"]`
  (§6) is the first instance: a typo'd `HOLD_STRATEGY` env value now fails at startup
  (pydantic-settings validation) instead of silently reaching
  `get_hold_strategy()`'s runtime `raise ValueError` on the first request that needs
  it. Any future service-selecting-a-strategy config value should follow the same
  pattern.
- **Every endpoint's auth requirement is explicit, never implicit.** Each route is one of:
  public, authenticated-only, `require_role("organizer")`, or ownership-scoped (role +
  owner-ID comparison, §15). Deciding "no guard needed" is fine; leaving it unstated by
  omission is not.
- **Log level discipline**: `debug` for normal flow/tracing, `info` for notable events,
  `warning` for expected/handled failures (validation rejections, business-rule
  `raise`s), `error`/`critical` reserved for genuine incidents. Don't log routine
  rejected-input paths at `error`.
- **Never query inside a loop.** Any endpoint assembling a response across multiple
  related records bulk-fetches first (`.in_()`-style filters), then assembles in memory.
- **Any multi-row INSERT or `.in_()`/subquery clause built from a list whose size isn't
  bounded by a small, fixed cap must batch through `chunked()`
  (`booking-service/app/db/chunking.py`), not assume the list stays small.** Postgres/
  asyncpg caps a single statement at ~32,767 bind params — found the hard way in Phase 3
  when a single unbatched multi-row `INSERT` (5 params/seat) overflowed that cap for any
  venue past ~6,500 seats, and a sweep's unbatched `.in_()` had the same latent risk
  against a large backlog. Use the shared `chunked()` helper and its
  `BIND_PARAM_SAFE_BATCH_SIZE` constant rather than each call site picking (and
  potentially drifting on) its own batch size — this was originally two independently
  duplicated magic numbers before being consolidated. Any future service with the same
  shape (a bulk insert sized by user input, a sweep over a potentially large ID list)
  should reuse or mirror this pattern, not reintroduce the bug.
- New service = copy the `event-service` template (built in P0.T5 — app factory,
  `core.py`, Manager+Repository layering), don't hand-roll a second pattern. For a
  service with no SQL/Mongo of its own (e.g. `search-service` — Elasticsearch is not a
  source of truth, §8), the `db/` folder still holds Repository classes with the same
  query/write-only discipline, just against a different client (an ES `Repository`
  instead of SQLAlchemy models) — the layering shape doesn't change, only what's
  underneath it.
- **Traefik routing: every new service after `event-service` gets a specific
  `PathPrefix` matching its actual resource routes** (e.g. `search-service` →
  `PathPrefix('/search')`), not a second catch-all. `event-service` itself keeps its
  original `PathPrefix('/')` as the implicit fallback — left as-is on purpose rather
  than retrofitted, since Traefik v3's default router priority scales with rule length,
  so any more-specific rule automatically wins over it for the paths it actually covers
  (no explicit `priority` label needed). Verify this via Traefik's own API
  (`GET :8080/api/http/routers`) when adding a new service's route, not just by
  assuming it works.
- **Integration tests use `testcontainers.community.*`, not the bare `testcontainers.*`
  namespace** — established in Phase 1 (`community.postgres`, `community.mongodb`) and
  extended in Phase 3 to `community.redis` and `community.kafka`. For Kafka
  specifically: `community.kafka.KafkaContainer("apache/kafka:3.8.0")` — the same
  image the compose stack actually runs — boots and works directly, no
  `.with_kraft()` override needed, unlike `search-service`'s existing suite, which
  uses a Confluent image (`confluentinc/cp-kafka`) plus `.with_kraft()` because that
  was the working combination found in Phase 2 before this was known. Worth revisiting
  `search-service`'s test infra to match at some point, but not a Phase 3 change — not
  touched here.
- **A Kafka consumer with its own DB write sets `enable_auto_commit=False` on the
  `AIOKafkaConsumer` and commits the offset manually, once per record, only after that
  record's handler has fully finished** — not on `aiokafka`'s default background timer,
  which advances offsets independent of whether the write underneath them actually
  succeeded. This is what makes "redelivery is a safe no-op" (the architecture invariant
  above) actually true under a crash mid-write, not just under a clean shutdown.
  Established in Phase 3 (`booking-service/app/kafka/consumers.py`) after a review found
  the default left in place; also wrap the DB write itself in a small bounded retry
  (a handful of attempts, short backoff) before giving up and letting the offset commit
  past a permanently-failed message anyway — without the retry, a merely transient DB
  error (a connection blip) costs that message's data on the very first hiccup, which
  defeats the point of disabling auto-commit in the first place. Any future consumer with
  its own DB write (Payment Service's webhook handler, most likely) should follow this
  same shape.
- Update this file after each phase checkpoint if conventions, commands, or structure
  shift — treat it as living documentation, not a one-time snapshot.

## Internal per-service layering — Decided

Lightweight version of the day-job pattern: **Manager + Repository per feature**, not
the full Manager/Logic/DBManager split. In the day-job version, Manager and Logic do two
different jobs despite sharing a folder: Manager is a thin dispatcher (which Logic class
handles this call), Logic is where `execute()`/`get()` and the actual business rules
live. Collapsing to one class merges both jobs into Manager:

- **The routing job** is cut because the one place this project genuinely needs to
  branch between alternate execution paths — the dual hold strategy — is already handled
  by the `TicketHoldStrategy` interface (§6). A second dispatch mechanism on top of that
  would be redundant. Every other feature here is single-path, so Manager-as-router has
  nothing to route.
- **The structuring job is not cut** — just no longer factored into its own class.
  Manager methods still read internally as the same sequenced, named-step shape
  `execute()` used to enforce (validate → fetch → mutate → build response); see the
  Manager bullet below.

Kept the Manager/Repository split itself because it's what keeps queries independently
testable and gives each feature a clean 2-class diagram for the report's Class Diagrams
chapter.

```
/services/{service-name}/app/
  main.py                    # app factory, router + consumer registration, startup/shutdown
  api/
    {feature}.py               # router — thin, no business logic
    schemas.py                  # Pydantic request/response DTOs, grouped by concern
  logic/
    {feature}_manager.py         # {Feature}Manager — one public async method per use case;
                                  # owns the transaction boundary (await session.commit());
                                  # constructor takes an existing session — never fetches
                                  # its own, since both API routes and Kafka consumers
                                  # construct it against a session they already hold
    helpers/                       # shared, non-endpoint-backing logic — hold-strategy
                                    # implementations, shared validators/resolvers
  db/
    models.py                       # SQLAlchemy models
    {feature}_repository.py          # query/write methods only, no business rules;
                                      # writes use flush(), never commit — Manager commits
  kafka/
    producers.py
    consumers.py                      # handlers construct the same {Feature}Manager the
                                        # API routes use — one business-logic path, two entry points.
                                        # Exception: a consumer with no equivalent API route (e.g.
                                        # search-service's EventConsumer — nothing else writes to
                                        # its index) calls the Repository directly; there's no second
                                        # entry point to unify with, so the rule's rationale doesn't
                                        # apply. Still exactly one place that talks to the datastore.
  core.py                              # settings, structured logging setup, async session
                                        # factory usable both via FastAPI Depends() and
                                        # directly inside Kafka consumer handlers
tests/
  unit/                                 # Manager methods, given a real or test session
  integration/                          # testcontainers — real Postgres/Mongo/Redis/Kafka
```

- DTOs: request/response Pydantic models live in `api/{feature}/schemas.py`. Enum-backed
  fields are typed as the real `Enum`, never `str` (see Conventions above). No separate
  logic-layer input DTO — Manager methods take typed kwargs or a schema instance directly;
  don't add an extra DTO class solely to pre-convert an enum's `.value`.
- `{Feature}Manager.__init__(self, session: AsyncSession)` — session always injected, never
  self-fetched. This is what makes it callable identically from a FastAPI `Depends()`-built
  session and from a Kafka consumer's own `async with session_factory() as session:` block.
- **A Manager method past the simplest CRUD case is still internally sequenced as named
  private steps** — `_validate()`, `_fetch()`, `_mutate()`, `_build_response()` called in
  order from the public method — even though this isn't factored into a separate Logic
  class. This is what preserves `execute()`'s readability discipline without the extra
  file per feature; don't let a Manager method collapse into one unstructured block just
  because the class boundary is gone.
- Repository methods never commit; Manager methods that mutate always end with
  `await session.commit()`. Same atomicity reasoning as the reference pattern — a Manager
  method may write through more than one Repository call before finishing.
