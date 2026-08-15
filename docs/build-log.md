# Build Log

Append-only, chronological diary of how this project actually got built — every
session, what was attempted, what failed, what worked, and any decision made
along the way that isn't big enough for `decisions-log.md` (which is locked,
§1–27, and reserved for architectural decisions made *before* implementation).
This file is the opposite: it's *during*-implementation reality, including the
dead ends.

Each entry is dated and tagged with the task it belongs to (per
`phases/phase-N-kickoff.md` / `master-development-plan.md`). Newest entries go
at the bottom.

---

## 2026-08-13 — P0.T1: Monorepo skeleton

Created the top-level layout per decisions-log §20: `/services` (five service
dirs + `_shared/auth`, each with an empty `app/` and placeholder `README.md`),
`/frontend`, `/infra`, `/docs`. Root `README.md`, `.gitignore`, `.env.example`.
Git initialized, first commit pushed.

**Worked:** straightforward — no surprises, matched the plan exactly.

---

## 2026-08-14 — Prerequisites re-check

Went back and actually verified the Prerequisites checklist item-by-item
instead of assuming it was done alongside P0.T1 (caught after initially
skipping it). Docker Desktop, Git+GitHub, and Claude Code confirmed genuinely
installed/authenticated. **Python 3.12 was not** — only 3.9.13 was on `PATH`,
no `python3.12`/`uv`/`pyenv` — left unticked and explicitly deferred rather
than faked as done. (Resolved later — see P0.T4 entry.)

---

## 2026-08-14 — P0.T2: Infra docker-compose

Built `/infra/docker-compose.yml`: Postgres (one container, three logical DBs
via `postgres/init.sh` templated from `.env`, not hardcoded — first draft used
a hardcoded `init.sql`, replaced with an env-driven shell script once the
credential-duplication problem was spotted), MongoDB, Redis, Elasticsearch
(heap capped, security off for local dev), Kafka in KRaft mode, Traefik v3
(dashboard on, no app services registered yet).

**Worked:** full stack brought up, health-polled, verified via `docker exec
psql \l` that all three DBs existed with correct ownership, Traefik dashboard
returned 200. Torn down cleanly afterward.

**Note:** this verification used `docker exec` (inside the container), not a
host-port connection — that distinction turned out to matter later (see the
port-collision entry under P0.T5).

---

## 2026-08-14 — P0.T3: Keycloak + realm

Added Keycloak (dev mode, `start-dev --import-realm`, embedded DB) plus a
realm export defining the `ticketing` realm, `user`/`organizer` realm roles, a
public PKCE frontend client, a confidential password-grant client
(`ticketing-service`), and three seed users (alice=user, bob=user+organizer,
carol=organizer).

**Failed, then fixed:** first import attempt let users log in via password
grant but Keycloak rejected every attempt with `invalid_grant: Account is not
fully set up`, even though `requiredActions` was explicitly empty on every
user. Root cause: Keycloak 25's User Profile feature silently requires
`firstName`/`lastName`; omitting them triggers a dynamic `VERIFY_PROFILE`
requirement that has nothing to do with the user's own `requiredActions` list.
Fixed by adding names to all three seed users — password grant then worked and
decoded claims showed the right realm roles.

---

## 2026-08-14 — Workflow correction: checklist-commit noise

User feedback: stop creating standalone commits whose only content is ticking
a `phase-0-kickoff.md` checkbox (e.g. "docs: mark P0.T3 complete"). Fixed
going forward, and retroactively — rewrote history (interactive rebase +
force-push, since already pushed) to fold every existing tick commit into the
code commit it verified. Also renamed one commit
("docs: seed decisions log, master plan, phase 0 kickoff, CLAUDE.md" →
"docs: seed project docs") since a substantive docs-content commit is a
different thing from a checklist tick and correctly keeps its own commit.
Standing rule now: tick checklist boxes by amending them into the adjacent
code commit before ever pushing, not as an afterthought commit.

---

## 2026-08-14 — P0.T4: Shared auth dependency

Built `services/_shared/auth` (`shared_auth` package): JWKS fetch + cache
keyed by `kid` (refreshes on unknown `kid`), RS256 signature + `iss`/`aud`/exp
validation via PyJWT, `get_current_user()` → `Principal` (subject + roles from
`realm_access`), `require_role("organizer")` factory → 403. 8 pytest unit
tests, JWKS fully mocked (real RSA keypair generated per-test, no live
Keycloak needed).

**Blocked, then fixed:** Python 3.12 was still missing (deferred from
Prerequisites). Installed `uv`, then `uv python install 3.12` — clean, no
system Python touched.

**Real gap found via live verification, not assumption:** brought Keycloak up
and actually decoded a token before writing the `aud` validation logic — the
realm's tokens carried **no `aud` claim at all**, since P0.T3's realm export
never configured an audience mapper. "Validate `aud`" was unimplementable as
written. Fixed by adding an `oidc-audience-mapper` to the `ticketing-service`
client (`aud: ticketing-services`), re-verified live before writing the
matching validation code.

---

## 2026-08-14 — Restructure: uv workspace

User caught that `_shared/auth` had its own isolated `.venv`/lockfile, and
every future service would repeat that pattern — duplicated venvs, no shared
dependency versions. Converted `/services` into a uv workspace: one root
`pyproject.toml` (`[tool.uv.workspace]`, `package = false`), one shared
`.venv`/`uv.lock`. Verified `uv run pytest` still works both from the member
package directory and from the workspace root (`uv run --package shared-auth
pytest`) — 8/8 still green.

---

## 2026-08-14 — P0.T5: event-service walking skeleton

Built `event-service` (Manager+Repository layering per CLAUDE.md — even for
the trivial `SELECT 1` healthcheck, to establish the real template other
services copy): `/healthz` (real DB check), `/metrics`
(`prometheus-fastapi-instrumentator`), `/demo/protected` (any authenticated
user), `/demo/organizer-only` (role-gated), structured JSON logging via
`structlog`, Dockerfile, wired behind Traefik via Docker labels in
`docker-compose.yml`.

This task surfaced three real, previously-latent environment problems — none
of which were visible until actually running things end-to-end rather than
trusting the plan on paper:

1. **Port collisions.** `localhost:5432` was already occupied by a
   Homebrew-managed Postgres@17 (unrelated to this project, silently
   shadowing the Docker container for host connections — this is exactly why
   P0.T2's `docker exec`-based verification never caught it). `5433` and
   `8001` were also taken, by another unrelated project's containers
   (`humbee_stack-*`). Remapped `POSTGRES_PORT` to `55432` in `.env`/
   `.env.example` rather than touching any pre-existing service.

2. **Traefik's Docker-labels discovery was completely broken.** Its container
   logged `Failed to retrieve information of the docker client and server
   host` in a permanent retry loop — the socket mount itself looked fine
   (labels correct, same Docker network, `docker version` worked fine from a
   plain container using the same mounted socket). Root-caused by inspecting
   a proxy's access log: Traefik's embedded Docker client hardcodes an
   initial `GET /v1.24/version` probe as its very first call, and this
   Docker Desktop build enforces `MinAPIVersion: 1.40`, rejecting anything
   older with a 400 *before* version negotiation can even begin — a
   chicken-and-egg failure. Bumping the Traefik image version and trying
   `DOCKER_API_VERSION` env vars didn't help (confirmed via access logs that
   the request was still literally `/v1.24/...` regardless). Fixed by adding
   a tiny nginx sidecar (`infra/docker-socket-proxy/`) between Traefik and
   the socket that rewrites away any `/vX.Y/` prefix, normalizing every
   request to the always-accepted unversioned endpoint — confirmed by curl
   testing several intermediate hypotheses (plain socket, `_ping` negotiation
   headers, explicit `/v1.51/info`) before landing on the actual fix.

3. **Only app-level logs were JSON — uvicorn's own request logs weren't.**
   `configure_logging()` only wired up `structlog`'s own processors; uvicorn's
   stdlib loggers (`uvicorn`, `uvicorn.access`) were untouched and kept
   emitting plain text. Fixed by routing stdlib logging through
   `structlog.stdlib.ProcessorFormatter` and replacing uvicorn's log handlers,
   so every line — app and access logs alike — is JSON.

**Verified for real, twice:** once incrementally while building, then again
with a full `docker compose down -v` + fresh `up -d --build` from nothing, to
confirm `main` really does stay bootable and the fixes weren't order-dependent
flukes. All done-when criteria passed both times: `/healthz` → 200 (via
Traefik), no token → 401, wrong role → 403, right role → 200, `/metrics` →
200, all logs JSON.

---

## 2026-08-14 — Introduced this file

User asked for a running build diary, separate from the finished code, so the
history of decisions/failures/fixes isn't lost once a task looks clean in
hindsight. Backfilled with everything from P0.T1 through P0.T5 above. Added a
DOCUMENT-step reminder to `CLAUDE.md` so future sessions keep it updated by
default rather than needing to be asked again.

---

## 2026-08-14 — P0.T6: Kafka smoke test

Wrote a standalone (own `pyproject.toml`, not part of the `/services` uv
workspace — this is infra tooling, not a service) pytest using `aiokafka`:
producer sends one JSON message, consumer (fresh `group_id`,
`auto_offset_reset="earliest"`) reads it back, asserts equality. Lives at
`infra/kafka-smoke-test/`.

**Failed, then fixed — caught by re-running, not by first-pass success.** The
test passed on the very first run, but a second consecutive run failed the
equality assertion. Root cause: the topic name was hardcoded
(`phase0-smoke-test`), so on the second run the topic already had the first
run's message sitting in it; with `auto_offset_reset="earliest"` and a brand
new `group_id` each run, the consumer correctly read the *oldest* message on
the topic — which was the previous run's, not the one just produced. Fixed by
generating a unique topic name per test run (`phase0-smoke-test-{uuid4}`),
which also matches the "throwaway" framing in the task prompt better than a
fixed topic name would have. Re-ran three times consecutively after the fix,
all green, to actually confirm the fix rather than trusting one clean run.

---

## 2026-08-14 — P0.T7: Dev workflow (Makefile, get-token.sh) — Phase 0 exit

Added a root `Makefile` (`up`/`down`/`logs`/`test`), `get-token.sh` (Keycloak
password-grant helper, defaults to seed user `alice`/`changeme`, hardcodes the
confidential `ticketing-service` client since that's the one with direct
grants enabled — the public frontend client can't do password grant at all),
and a "Local Development" section in the root README.

**Verified as a genuine clean-checkout simulation, not just re-running in the
existing shell:** deleted `.env`, ran `make up` (confirmed it auto-copies
`.env.example`), got a token with zero args (`alice`) and with explicit args
(`bob`), hit both `/demo/protected` and `/demo/organizer-only` with the
returned tokens, confirmed a wrong-password call fails loudly (non-zero exit,
no bogus token printed) instead of silently succeeding. Also re-ran `make
test` inside a real `zsh -l` login shell rather than the working shell's
already-exported `PATH`, since `uv` was installed mid-session (P0.T4) and a
prior turn's manual `export PATH=...` could have been masking a real gap for
a genuinely fresh terminal.

**Phase 0 exit checklist:** all six items ticked — every one had already been
independently verified live across P0.T1–T7 (stack boots, auth round-trips
end-to-end, role enforcement, JSON logs + metrics, Kafka round-trip, `main`
bootable at every commit). Phase 1 (Event Service) is next.

---

## 2026-08-14 — Reorg: `docs/phases/`

User asked for a standing rule against any folder becoming a flat pile of
files as the project grows. `/docs` was the obvious near-term risk: with 11
phases total, `phase-N-kickoff.md` would eventually mean 11 files sitting
alongside `decisions-log.md`, `master-development-plan.md`, `build-log.md`,
and `architecture.html` in one flat directory. Moved `phase-0-kickoff.md` →
`docs/phases/phase-0-kickoff.md` now, while there's only one file to move,
rather than after several more pile up. Every future phase kickoff doc goes
there from the start. Updated all references (`README.md`, `CLAUDE.md`,
`build-log.md`'s own intro line) except this file's historical entries, which
describe what was true at the time and stay as written.

---

## 2026-08-14 — Caught up: report drafting had been skipped

User asked what's startable on the report now that Phase 0 is done. Answering
that surfaced a real process miss: both `CLAUDE.md` and
`master-development-plan.md` §2 already required drafting the report section
each phase feeds *during* that phase's DOCUMENT step — not just capturing
evidence for later — and that never actually happened across P0.T1–T7.
`build-log.md` and `architecture.html` were kept current; report prose was
not, despite being asked for the whole time.

Fixed by starting `docs/report/` (chapter-per-file, status table in its
README, per the new folder-hygiene convention) and drafting the two sections
Phase 0's evidence actually supports right now: Project Description
(partial — full version needs P1+P2 too, per Milestone A's real scope) and
Technologies Used (a running list, appended each phase from here). Did not
draft Requirement Gathering — it's genuinely blocked on P1.T5's roles table,
not just unstarted.

---

## 2026-08-14 — Academic-presentation scan before Phase 1

User asked for a full scan of the repo and commit history for anything that
would read badly on an MS capstone submission, and to lock in hard rules
before starting Phase 1. Findings: no secrets/credentials ever committed, no
accidentally-tracked build artifacts, thorough `.gitignore`, clean commit
messages (no phase/task IDs leaking in, consistent `type: description`
style except two pre-convention early commits). Real issues found: commit
author showing as `STRYKER316` (a handle) on 11 of 13 commits instead of a
real name; `✅` emoji used as a status marker in one doc, inconsistent with
the plain `- [x]` checkboxes used elsewhere in the same file; a default
Apache 2.0 `LICENSE` file of uncertain fit for graded coursework under
review.

Fixed: removed `LICENSE`; replaced the emoji markers with plain text
(`**Verified.**`); reworded the two pre-convention commit messages
("Initial commit", "Add monorepo skeleton...") to match the established
style — all via one more rebase + force-push, same pattern as the earlier
history cleanups. **Explicitly left as-is, by the user's own choice:**
commit author identity stays `STRYKER316` — flagged, not silently "fixed."
**Explicitly kept, by the user's own choice:** "Claude Code" mentioned by
name throughout `/docs`, as a deliberate transparency stance rather than
something to minimize. Locked both the fixes and the deliberate
non-fixes into a new CLAUDE.md section ("Academic-presentation hard rules")
so this doesn't need re-litigating each phase, and to stop me from
"fixing" the author-identity choice unprompted later.

---

## 2026-08-14 — Markdown cleanup pass

User asked for a check across every tracked `.md` file (16 total) for staleness
— cross-references, broken links, contradictions, drift after today's several
reorgs (docs/phases, docs/report, architecture.html). Checked internal links,
backtick-quoted file paths against what actually exists, code-fence balance,
and read every file that had touched anything today.

Found and fixed:
- `README.md`'s layout tree didn't mention `docs/report/`, added after this
  session.
- `infra/README.md` still said "App services aren't wired in yet" directly
  above a table listing `event-service` — a straight self-contradiction left
  over from before P0.T5.
- `services/_shared/auth/README.md` documented `AUTH_KEYCLOAK_ISSUER`/
  `AUTH_EXPECTED_AUDIENCE`/`AUTH_JWKS_CACHE_TTL_SECONDS` but not
  `AUTH_JWKS_URI_OVERRIDE` — a real config field (added mid-P0.T5 for the
  Docker-internal JWKS fetch) that existed in code but nowhere in its own
  README.
- `CLAUDE.md`: "copy the Phase-0 service template (once it exists)" — the
  template has existed since P0.T5, the parenthetical was stale. Also removed
  four HTML-comment placeholder lines ("Fill in as they stabilize
  post-Phase-0") whose trigger condition had been met for a while and whose
  content already lives in `README.md`'s Local Development section (one of
  the stale placeholder lines even pointed at the wrong path,
  `./infra/get-token.sh`, when the real file is at repo root). Also added
  `uv` to the Tech Stack list — in active use since P0.T4 but never listed.

No broken links, no unbalanced code fences, no large/binary files, service
placeholder READMEs (booking/payment/notification/search) all still
accurate as unbuilt stubs. `services/event-service/README.md`'s documented
run command was tested live, not just read — starts cleanly.

---

## 2026-08-14 — Commit-granularity rule, and squashing the commit-spam it fixes

User called out that recent commits looked redundant — several small
doc-only fixes (a folder-org note, a LICENSE removal, an emoji swap, a
build-log catch-up entry) had each landed as their own commit instead of
being batched with the substantive commit they were really part of. This is
a broader version of two earlier, narrower rules (checklist ticks get folded
into code commits; build-log entries get folded into code commits) — both
of which were themselves fixes for the same underlying pattern recurring at
a smaller scale. Generalized this time instead of patching the specific
case again: **every commit is one real, complete unit of work** — code, or a
doc change substantial enough to stand alone — never a bare mechanical tweak
riding solo, and never an entire session crammed into one giant commit
either. Locked into `CLAUDE.md`'s Conventions section so it's visible
project-wide, not just in my own memory — retired the two narrower memory
entries this supersedes.

Squashed three redundant clusters via rebase + force-push (same pattern as
every prior history cleanup this session):
- `docs/phases/` move + the folder-organization convention note → one commit
  (both were the same request, split for no reason).
- `docs/report/` creation + the "report drafting had been skipped" build-log
  catch-up → one commit (again, same request).
- LICENSE removal + emoji-marker cleanup + the academic-presentation
  hard-rules writeup → one commit (all three were one continuous scan-and-fix
  pass, artificially split into three).

10 commits in that range collapsed to 6, with nothing lost — same end state,
verified via `git status` (clean) and spot-checking that `LICENSE` stayed
gone and `docs/report`/`docs/phases` both still existed post-squash.

---

## 2026-08-14 — Process decisions before Phase 1: TDD scope, code-review scope

User asked whether the superpowers skill set (brainstorming, TDD, systematic-
debugging, etc.) is needed from Phase 1 onward, or whether the phase kickoff
docs + `CLAUDE.md` already drive the work sufficiently. Answer: mostly the
latter — this project is already heavily pre-planned (locked architecture,
task-by-task prompts with explicit done-when criteria), so the
planning-oriented skills would be redundant ceremony on top of a plan that
already exists. Two real open questions got surfaced instead of skills:
test-first vs. build-then-test, and whether to add a dedicated review pass
anywhere. User delegated both decisions.

**Decided, not blanket:** test-first specifically for the dual hold
strategies (P3) and payment/webhook idempotency (P4) — the two places where
writing the correctness contract as a test before the implementation
actually clarifies the contract, and where a subtle bug wouldn't just fail
loudly, it would silently corrupt the guarantee the whole project is built
around (no double-booking) or double-charge/under-refund a customer.
Build-then-test everywhere else, deliberately choosing *not* to fight
`master-development-plan.md`'s own task sequencing (a dedicated Tests task
at the end of each phase's list implies build-then-test was already the
plan's assumption for routine work).

**Dedicated `/code-review` pass, not self-verification alone, on P3 and P8
only** — dual-hold because it's the one bug class that corrupts the core
product guarantee silently rather than failing loudly, and the benchmark
because its numbers are the report's only Measured chapter and errors there
are load-bearing for the entire report, not just one feature. Every other
phase keeps the self-verification-via-live-testing pattern already
established and working across all of Phase 0.

Both written into `CLAUDE.md`'s Workflow & cadence section as scoped rules
(not "always TDD" / "always review," which would have been the easy but
wrong answer) so they apply consistently from Phase 1 without needing to be
re-decided per phase.

---

## 2026-08-14 — Full commit-history audit against the granularity rule, Phase 0 closeout

User asked for a full read of every commit (not just the recent ones already
squashed) against the commit-granularity rule, before moving to Phase 1.
Read all 20 commits chronologically with file-change stats for each.

**One clear violation found:** `event-service: update README past
placeholder status` (1 file, 14 lines) directly followed the walking-skeleton
commit in the same session, documenting the very thing that commit built —
textbook case of what the rule forbids. Folded it in via one more rebase +
force-push.

**One borderline case, judged and left as-is:** the small `init.sql`
reference/docker-socket-proxy correction commit — small, but a complete
response to its own distinct user request, with nothing adjacent at the time
to batch it with (unrelated commit before, distinct new deliverable after).
The rule targets fragmenting one piece of work or letting small fixes pile
up unbatched, not every small commit on principle — this one didn't fit
either failure mode, so it stayed.

Everything else checked clean: no commit message body paragraphs anywhere
in history, no phase/task-ID tags leaking into any of the 20 messages, no
message sounding doc-only when it wasn't. This closes out Phase 0 — every
task verified live, every doc cross-referenced and current, commit history
clean against the rules established this session. Next: Phase 1 (Event
Service).


---

## 2026-08-15 — Phase 1: Event Service

Generated `docs/phases/phase-1-kickoff.md` from the master plan's Phase 1
section (the pattern established at the end of Phase 0), then ran all seven
tasks in order, verifying each live before moving on.

**P1.T1** — extended the P0 template with a Motor client (`get_mongo_db()` in
`core.py`), wired `mongodb` into the compose entry, removed the now-obsolete
`/demo/*` routes and `DemoResponse` schema, and folded a Mongo `ping` into
`/healthz`. Verified: `/healthz` returns 200 with both DBs reachable; `/demo/*`
now 404s.

**P1.T2** — Postgres schema (`Event`, `Venue`, `Performer`, plus the
`event_performers` association table) as async SQLAlchemy models, first
Alembic migration, and a thin Repository layer per feature. Hit one real bug:
`migrations/env.py`'s autogenerate scaffold unconditionally overwrote
`sqlalchemy.url` from `get_settings()` on every load — harmless for the plain
CLI case but silently clobbered any URL a caller passed in explicitly. Fixed
by preferring `config.attributes["sqlalchemy_url"]` when a caller sets it,
falling back to `get_settings()` otherwise. This turned out to matter later
in P1.T6 (see below) — worth being explicit about now rather than
rediscovering blind.

**P1.T3** — MongoDB seat-map documents: sections → rows → seats, validated at
the boundary via Pydantic (`SeatMap`/`SeatMapSection`/`SeatMapRow`/`Seat` in
`api/schemas.py`) before ever touching Mongo. `SeatMapRepository` stores/reads
whole documents keyed by `event_id`. Verified live: store, fetch, delete
round-trip against the real `mongodb` container.

**P1.T4** — public read APIs (`EventManager`, `VenueManager`): list with
pagination + sortable by `start_time`/`title` in either order, event detail
(with venue + performers eagerly loaded, not lazy — no N+1), seat-map fetch,
venue detail. Verified live against five seeded events: paging, both sort
orders, 404 on a missing seat map.

**P1.T5** — organizer write APIs (`POST`/`PATCH`/`DELETE /events`) with
ownership scoping per §15: `require_role("organizer")` at the route plus an
explicit `event.organizer_id == user.subject` check inside `EventManager`
before any mutation. DTOs (`EventCreate`/`EventUpdate`) reject blank titles
and past `start_time` via Pydantic validators — a 422, not a downstream
failure. `DELETE` has the "zero bookings" guard clause structured as its own
private step (`_check_no_bookings`) but is a no-op for now since Booking
Service doesn't exist until Phase 3 — real check lands there, not tracked as
a code TODO. Verified live end-to-end with real Keycloak tokens for
alice/bob/carol: non-organizer 403, cross-owner 403 on both update and
delete, owning organizer succeeds; also verified the DTO validators reject a
past `start_time` and a whitespace-only title with 422.

**P1.T6** — unit tests mock the Repository layer to exercise
`EventManager`'s ownership branch in isolation (owner succeeds, non-owner
403, both update and delete); integration tests spin up real Postgres +
MongoDB via `testcontainers-python` (`driver="asyncpg"` on `PostgresContainer`
so the same async engine code path runs against the container as against
compose) and exercise the full create → fetch → seat-map flow plus the
DB-backed ownership check. This is where the `migrations/env.py` bug from
P1.T2 actually bit — without the fix, the container's Alembic run silently
connected to whatever `.env` happened to have sourced into the shell instead
of the container's own generated URL. First case in this repo of
testcontainers-python; established the pattern (session-scoped container
fixtures, migration run once per session, table truncation between tests)
for Phases 2+ to reuse. 7/7 tests green.

**P1.T7** — seed script (`app/seed.py`, `make seed`) populates two venues,
three performers, three events (one past, two future) and one seat map;
checks for existing data first so re-running is a no-op. Verified live
through the running API, including the idempotency check.

**Process note:** no dedicated `/code-review` pass this phase — that's
reserved for P3 and P8 per the decision logged 2026-08-14. Self-verification
via live testing (real tokens, real containers, real HTTP calls) was used
throughout, consistent with every phase so far.

Updated `docs/architecture.html` to current state: `event-service` now shows
both Postgres and MongoDB wired (not just Postgres), the auth-flow diagram
runs against the real `POST /events` route instead of the Phase 0 demo
route, and a new note covers ownership scoping (carol blocked from bob's
event) since role-only gating was the whole story last phase and isn't
anymore. Reproduce-yourself commands updated to the real API. This closes
out Phase 1 — every task verified live, tests green, seed data browsable
end to end. Next: Phase 2 (Search Service + Kafka #1).


---

## 2026-08-15 — Standing phase-end checklist, and closing the review gap on Phase 1

User asked where PR review fits into the workflow, since nothing had been
discussed — the honest answer was: nowhere, routine phases relied on
self-verification alone, with `/code-review` reserved for P3/P8 only. Rather
than leave that as an open gap, added a **Phase-end checklist** to
`CLAUDE.md`'s Workflow & cadence section: seven items to run through before
every CHECKPOINT (end-to-end testing, report writing, commit/file rule
scanning, `architecture.html` currency, decisions-log delta check, CLAUDE.md
self-update check, and — item 7 — a `/pre-pr` review gate: simplify →
code-review → verify run against the phase's accumulated diff, since there's
no branch/PR workflow here to hang a review off of). This runs in addition
to, not instead of, the dedicated P3/P8 adversarial review.

Since item 7 didn't exist when Phase 1 checkpointed, ran it retroactively
against the full Phase 1 diff (`45d6597..5adc087`) to close the gap rather
than starting clean only from Phase 2:

**Simplify pass** (two rounds — the first was interrupted mid-run and picked
back up): extracted a shared `BaseRepository[ModelT]` (create/get_by_id/
get_many_by_id/delete) that `VenueRepository` and `PerformerRepository` now
inherit, deduplicating identical CRUD boilerplate; `EventRepository` stayed
separate on purpose since it always eager-loads relationships. Also removed
a couple of unused imports and hoisted one inline import.

**Code-review pass** (Opus, against CLAUDE.md's actual conventions, not a
generic pass) surfaced real findings, not just style: a timezone-comparison
bug where a naive `start_time`/`end_time` in a request body crashed with an
uncaught `TypeError` (500) instead of a clean 422, since Pydantic only
auto-converts `ValueError`/`AssertionError` to a validation error and the
comparison against `datetime.now(timezone.utc)` raised neither; an orphaned-
MongoDB-document bug where `delete_event` removed the Postgres row but never
called `SeatMapRepository.delete`, so a recycled `event_id` could resurrect
a stale seat map; unstable pagination (`list()` sorted by `start_time`/
`title` alone with no tiebreaker, so rows sharing a sort value could
duplicate or vanish across pages); `SeatMap.event_id` typed as bare `str`
for a UUID-valued field, violating the DTO-strictness convention; `seed.py`
using `print()` instead of structlog; no `warning`-level logging on
`EventManager`'s rejection paths; and `docs/report/class-diagrams.md` left
stale by the simplify commit. One design question (public reads return
`DRAFT` events since no publish path exists yet — is that acceptable for
Phase 1, or does it need gating now) was surfaced but deliberately left
for a scope decision rather than silently fixed.

Fixed everything except the scope question and the two commit-message nits
(a stray "Phase 1" in one subject line, one commit that arguably should've
been folded into its predecessor) — decided to leave git history alone
rather than rewrite already-settled commits for a process gap that didn't
exist yet when they were made. Six commits: naive-datetime rejection (with
a new regression test proving the fix — deliberately reproduces the exact
crash first, to confirm the test would have caught it), the seat-map-delete
fix bundled with the UUID retyping and the new warning logs (all three
landed in the same `EventManager` methods, splitting further wasn't worth
the git surgery), the pagination tiebreaker, the conftest cleanup, and the
class-diagrams doc update.

**Re-review** (a second Opus pass, checking the fixes against the original
findings rather than re-reviewing from scratch) confirmed all five fixed
correctly, flagged one real miss: the integration test asserting seat-map
deletion was actually passing for the wrong reason — `get_seat_map` 404s on
the *event* lookup first (already gone), before ever reaching the seat-map
check, so the test never actually proved the Mongo document was deleted.
Fixed by asserting directly against `SeatMapRepository.get_by_event_id`
instead of routing through the manager. The re-review also named a residual
risk worth stating plainly rather than hiding: the Mongo delete runs after
the Postgres commit, so a Mongo-side failure between the two still leaves an
orphan — narrower than before, not eliminated, and an accepted trade-off of
the no-distributed-transactions architecture invariant rather than a bug to
chase further.

10/10 tests green throughout (7 original + 3 new). Live-verified the two
behavioral fixes (naive-datetime 422, seat-map deletion) against the running
stack with real curl calls, not just the test suite. This is the first time
the Phase-end checklist's item 7 has run — establishes the pattern for
Phase 2 onward.


---

## 2026-08-15 — Closing the review-gate gap on Phase 0 (shared_auth, service template)

User asked whether Phase 0 needed the same retroactive treatment as Phase 1
since it also predated the `/pre-pr` checklist item. Recommended scoping it
tighter than Phase 1's pass — most of Phase 0 is infra/config
(docker-compose, Keycloak realm export, Makefile), not code-review
territory — and focusing on `shared_auth`, the JWT-validation dependency
every service imports, since a bug there is a security bug system-wide, not
local. User agreed and asked to tackle findings "as it fits best" rather
than pre-negotiating scope item by item.

**Simplify pass**: reused the JWKS `httpx.AsyncClient` instead of creating
one per refresh (was paying a full handshake on every TTL expiry or unknown-
kid lookup), parallelized the Postgres+Mongo `/healthz` pings via
`asyncio.gather`. Two findings deliberately skipped with reasoning recorded:
narrowing `health_manager.py`'s broad `except Exception` (a health check
should stay broad — narrowing risks an unusual driver exception escaping
instead of correctly reporting 503), and unifying `core.py`'s two singleton
patterns (`lru_cache` for settings vs. manual `global`/`is None` for
engine/mongo — they solve genuinely different lifecycle needs).

**Code-review pass**, run with an explicit security/attacker lens (not just
a style pass) against `shared_auth`, found 16 real findings. The two that
mattered most: `_fetch()` failures (Keycloak down, malformed JWKS body) were
completely uncaught, surfacing as a 500 with a stack trace instead of a
clean 401/503 — the auth path had zero resilience to its own dependency
being unavailable; and the newly-long-lived JWKS `httpx.AsyncClient` (from
the simplify pass) was never closed anywhere, a real leak inconsistent with
this codebase's explicit-cleanup discipline elsewhere. Also found: no lock
around JWKS refresh (thundering herd + a racy double-client-creation
window), an unknown-`kid` DoS amplifier (unbounded 1:1 request-to-Keycloak-
fetch), zero `warning`-level logging anywhere in the auth path, a raw PyJWT
exception string leaking into the 401 response body, a malformed
`realm_access` claim crashing instead of failing closed to no roles, JWKs
missing the optional `use` field silently dropped, bare `str` config fields
where the DTO-strictness convention calls for constrained types, and —
notably — `jwks.py` (the trickiest logic in the package: TTL, refresh,
rotation) had *zero* real test coverage, since the existing test stub fully
overrode `get_key()` rather than exercising the real cache.

Fixed all 16: added a `JWKSFetchError` boundary around the fetch (initially
incomplete — see below), an `asyncio.Lock` around refresh with double-check
after acquire, a 1-second minimum-refetch-interval throttle for the DoS
case, `aclose()` wired through to `event-service`'s shutdown lifespan,
`warning`-level logging on every rejection path, fixed 401 messages instead
of leaked exception text, defensive `realm_access`/`roles` parsing that
fails closed, the `use`-field fix, `NonBlankStr` constraints plus trailing-
slash normalization on `AuthSettings`, and two rounds of new tests: a real
`jwks.py` suite via `httpx.MockTransport` (fetch/cache, TTL, unknown-kid,
`use` field, fetch failure, the DoS throttle, `aclose`), and negative auth
tests including a genuine algorithm-confusion attack — hand-constructing a
forged HS256-signed JWS using the RSA public key as the HMAC secret, since
PyJWT's own `encode()` refuses to build that token via its normal API (a
real attacker wouldn't go through PyJWT's guard rails either). Also fixed
three smaller `event-service` findings: `dispose_engine()` not nulling
`_engine`/`_session_factory` (asymmetric with `close_mongo_client()`), log
level hardcoded to `INFO` with no way to get `debug` output, and
`asyncio.gather` on the health pings without `return_exceptions=True`
(leaving a sibling task un-awaited on failure). 7 commits, each scoped to
one coherent fix.

**Re-review** caught one real remaining gap: the fetch-error `try` block
wrapped the HTTP call but not the key-parsing comprehension right after it —
a 200 response with a malformed body (missing `kid`, unparseable key
material, `keys` not a list) still crashed with a raw `KeyError`/
`ValueError`/`AttributeError` instead of becoming the intended
`JWKSFetchError` → 503. Fixed by moving the parsing inside the `try` and
broadening the caught exception types; verified against all three failure
modes directly before adding them as permanent regression tests. Two other
re-review notes deliberately left as-is: `aclose()` isn't lock-guarded and
doesn't null `_client` (shutdown-only, not exploitable), and a `"/"` issuer
value normalizes to an empty string past the trailing-slash strip before
the non-blank check re-runs (degenerate input, cosmetic).

28/28 `shared_auth` tests green (12 new), 8/8 `event-service` unit tests
green. Live-verified against the real Keycloak instance: valid tokens still
authenticate, role denial still 403s, a malformed token now 401s with the
real error visible only in the structured JSON log (`auth_token_malformed`,
`warning` level) — not leaked to the client. This closes the review-gate
gap on both Phase 0 and Phase 1; Phase 2 onward gets the check live from
the start.

---

## 2026-08-15 — P2.T1: Event Service Kafka producer

Generated `docs/phases/phase-2-kickoff.md` from the master plan's Phase 2
section, then started on P2.T1.

Before writing any producer code, hit a real design gap: Phase 1 shipped
`Event.status` as `DRAFT`/`PUBLISHED` with `POST /events` defaulting to
`DRAFT` and no publish path, flagged at the Phase 1 review as an open scope
question rather than fixed. Decisions-log §15 says "creation = publishing,
no separate draft/review state" — the two disagreed, and P2.T1 needed a real
answer for when an event becomes visible to Kafka/Search. Asked the user
directly rather than guessing; chose to keep the two-state model and add a
dedicated publish step, logged as an amendment under §15 (decisions-log
delta, not just a build-log note, since it genuinely extends a locked
decision).

Implementation: `EventProducer` (`app/kafka/producers.py`) wraps a lazily-
started, module-singleton `AIOKafkaProducer` (same lifecycle pattern as the
existing Mongo client singleton in `core.py`), keyed by event ID for
ordering/idempotency per §7. Messages are event-carried state transfer per
§7.2 — the full seat list (section/row/seat label), flattened from the Mongo
seat-map document, travels with the event rather than a venue reference.
Added `POST /events/{id}/publish` (organizer-only, ownership-scoped,
`DRAFT`→`PUBLISHED` one-way, 409 if already published, 422 if no seat map
exists yet — publishing without one would mean the Kafka payload can't
actually carry a seat list). `update_event` re-publishes only if the event
is currently `PUBLISHED` (a `DRAFT` edit has nothing to sync); `delete_event`
fires a delete message only if the event was `PUBLISHED` before removal.
`EventManager` now takes the producer as a constructor-injected dependency
alongside the session and Mongo handle, consistent with "never self-fetched."

`aiokafka` added to `event-service`'s dependencies; `KAFKA_BOOTSTRAP_SERVERS`
+ `EVENTS_TOPIC` wired into its compose entry, with `kafka: service_healthy`
added to `depends_on` (event-service now genuinely depends on Kafka being up,
not just Postgres/Mongo/Keycloak).

Verified live against the real stack, not just the test suite: rebuilt and
recreated the `event-service` container (the running one was still Phase-1
code — a `curl` against `/publish` first came back a bare FastAPI 404 before
the rebuild, which is what caught it), created a `DRAFT` event as carol,
confirmed `POST /publish` 422s with no seat map, inserted a seat-map document
directly via `mongosh` (no write API for seat maps exists yet — same gap
`seed.py` already works around), published it, and read the real message off
`event.events` with `kafka-console-consumer` — full payload, correct
flattened seats. Then confirmed: publishing twice 409s; a `PATCH` on the now-
`PUBLISHED` event produces a second `upserted` message with the new title;
deleting it produces a `deleted` message. 15/15 `event-service` tests green
(4 new unit tests for the publish 409/422/success paths, 2 new integration
tests for the republish-on-update and delete-notification behavior against
real Postgres+Mongo, existing tests updated for the new constructor
parameter).

---

## 2026-08-15 — P2.T2: Search Service scaffold + Elasticsearch client + index mapping

Scaffolded `search-service` from the `event-service` template (app factory,
`core.py`, `api`/`db`/`logic` layering, `/healthz`, `/metrics`, structured
logging) per the "new service = copy the template" convention, adapted for a
service with no Postgres/Mongo of its own (§8 — Elasticsearch is not a
source of truth): `db/event_index_repository.py` holds the index mapping and
an `ensure_index()` call instead of SQLAlchemy models, and `/healthz` pings
Elasticsearch's `info()` instead of a DB `SELECT 1`. The index mapping
mirrors the event-carried Kafka payload from P2.T1 (`event_id`, `title`,
`description`, `start_time`/`end_time`, `venue_name`, `performer_names`, and
a `nested` `seats` field for section/row/label) — no business-logic Manager
yet, since there's nothing to orchestrate until the consumer (P2.T3) and
search API (P2.T4) exist. Skipped adding `shared_auth` entirely: every
search-service route is public, so there's no JWT validation to wire up
(the "auth requirement explicit" convention is satisfied by never depending
on it, not by a no-op check).

Real bug caught during this task, not a hypothetical: `pydantic-settings`
isn't a direct dependency of anything `search-service` uses (`fastapi`,
`elasticsearch`, `structlog`, `prometheus-fastapi-instrumentator`) — it only
worked for `event-service` because `shared-auth` pulls it in transitively.
Missed on the first build, caught immediately by the container crash-looping
on `ModuleNotFoundError: No module named 'pydantic_settings'` when actually
run, not left latent. Added it as an explicit dependency.

Also hit a real routing question the moment a second service joined Traefik:
`event-service`'s router rule is `PathPrefix('/')`, a catch-all that was only
safe while it was the only registered service. Rather than touch
`event-service`'s already-checkpointed Phase 1 routes, gave `search-service`
its own `PathPrefix('/search')` rule and confirmed via Traefik's API
(`/api/http/routers`) that it gets higher priority (21 vs. 15) than the
catch-all — Traefik v3's default priority scales with rule length, so no
explicit `priority` label was needed. `/search` doesn't exist as an endpoint
yet (P2.T4), but the routing precedence is proven correct now, before
there's real traffic to get it wrong on.

Verified live: `docker compose build` + `up` from a clean image, watched the
startup log show `HEAD /events` (404) → `PUT /events` (200) → "Application
startup complete" — the index is created idempotently on boot, not just
asserted to work. Confirmed the mapping via `GET /events/_mapping` matches
what was defined, `/healthz` returns `{"status": "ok"}` hitting the real
Elasticsearch container, and `event-service`'s existing routes (`/healthz`
→ 200) are unaffected by the new router. No dedicated test suite for this
task — matches P1.T1's "wiring only" precedent (verified live, not unit-
tested) since there's no business logic yet to unit test; P2.T5 adds the
real integration coverage once the consumer and search API exist.

---

## 2026-08-15 — P2.T3: Search Service Kafka consumer (idempotent)

`EventConsumer` (`app/kafka/consumers.py`) parses the raw Kafka payload,
dispatches on the `action` field to either `EventIndexRepository.upsert`
(indexes by event ID — a redelivered upsert overwrites the same document
rather than creating a duplicate, §7) or `.delete` (wrapped in a
`NotFoundError` catch, so a redelivered or out-of-order delete for an
already-gone document is a safe no-op, not an error). Deliberately no
Manager class here, per the phase-kickoff note — ES upsert/delete by ID *is*
the operation, nothing to orchestrate above it. Per-message exceptions are
caught and logged at `error` level inside the consume loop rather than
propagating, so one malformed or failing message can't kill the whole
background consumer task (no retry/DLQ machinery — that's Notification
Service's problem per §17, not Search's). Wired into `main.py`'s lifespan as
a background `asyncio.Task`, cancelled cleanly on shutdown.

Live-verified redelivery end to end against the real stack, not just mocks:
published a real event through `event-service`, watched `search-service`'s
own logs show the upsert land in Elasticsearch; then hand-crafted the exact
same Kafka message via `kafka-console-producer` and replayed it — document
count stayed at 1, `_version` incremented (proving overwrite, not
duplication). Same for delete: deleted the event, confirmed the document
gone, replayed a duplicate delete message, and confirmed Elasticsearch's
404 was caught and logged cleanly with no crash.

Hit a real, non-obvious infrastructure bug while building the integration
test suite (`testcontainers` + real Kafka + real Elasticsearch): a fresh ES
index sat at cluster status `red` (`active_primary_shards: 0`, then still
unassigned after a 30s `wait_for_status=yellow`) no matter how long the test
waited. Root-caused via `docker system df -v`: Docker Desktop's VM disk was
at 90% usage (125.7GB allocated, only 11.7GB free) because of one orphaned,
unattached 101.6GB anonymous volume, unrelated to this project — Elasticsearch's
disk-based shard-allocation watermark (low/high at 85%/90%) was correctly
refusing to allocate the primary shard onto a node that looked full. Not a
code bug or a flaky test; flagged to the user before touching anything,
since pruning Docker resources is a system-wide action. User approved a safe
prune; `docker volume prune` (only removes volumes with zero container
references, never touches anything in use) reclaimed the 101.6GB and dropped
usage to 9%. Also hardened `EventIndexRepository.ensure_index()` itself as a
result — it now sets `number_of_replicas: 0` on index creation (correct for
this project's single-node ES topology, §12/§24: a replica could never be
assigned to a second node that doesn't exist, so it would sit unassigned and
hold cluster health at `yellow` forever for no reason) and blocks on
`cluster.health(wait_for_status="yellow")` before returning, so neither a
real deployment nor a test can observe an index that looks created but isn't
actually shard-ready yet. This is a real production robustness fix the
disk-space incident surfaced, not just a test workaround.

7/7 `search-service` tests green (5 new unit tests mocking the repository —
upsert dispatch, delete dispatch, malformed JSON, unknown action, and a
repository exception all handled without raising; 2 new integration tests
against real `testcontainers` Kafka + Elasticsearch covering the eventual-
consistency window and both redelivery cases). One test infra note: reused
`testcontainers`' `KafkaContainer` needed `confluentinc/cp-kafka:7.6.0` with
`.with_kraft()` rather than the `apache/kafka` image the compose stack uses
directly — that container class's bootstrap scripts are Confluent-image-
specific and the `apache/kafka` image exits immediately under it; not a
concern for the real stack, which configures `apache/kafka` by hand in
`docker-compose.yml` already and doesn't go through this test helper.

---

## 2026-08-15 — P2.T4: Search API

`GET /search` (public, `SearchManager` + `EventIndexRepository.search()`)
over free text (`q`, multi-match across `title`/`description`/`venue_name`/
`performer_names`, `match_all` when blank — browse-all with no query typed),
paginated (`limit`/`offset`), sortable by relevance (`_score`, the default)
or `start_time`, both directions. Query construction lives in the
Repository (it's still just "the query," same as `EventRepository.list()` in
`event-service`); `SearchManager` shapes raw ES hits into `SearchResponse`
DTOs — same split as every other service. Reused the Phase 1 pagination
lesson directly this time instead of rediscovering it: every sort always
carries `event_id` as an explicit tiebreaker, so ties (two events with
identical relevance or identical `start_time`) can't duplicate or drop rows
across pages.

Live-verified end to end through the real stack, not just unit tests:
created and published three events via `event-service` ("Jazz Night at
Riverside", "Rock Festival Weekend", "Classical Jazz Trio"), queried
`GET /search?q=jazz` through Traefik and got back exactly the two jazz
events, not the rock one; verified `start_time` pagination returns distinct,
correctly-ordered pages (`limit=2` then `limit=2&offset=2`) over the full
3-event set; deleted all three afterward and confirmed the index emptied
back out. 3 new unit tests for `SearchManager` (hit-to-DTO mapping,
argument pass-through for pagination/sort, empty-result shape) — no new
integration test needed here specifically, since P2.T3's redelivery
integration test already exercises the exact "publish → becomes searchable"
path P2.T5 calls for (same Kafka-to-Elasticsearch pipeline `/search`
queries read from); noted for P2.T5 rather than duplicating it. 10/10
`search-service` tests green.

---

## 2026-08-15 — P2.T5: Tests

The integration coverage this task calls for (testcontainers Kafka +
Elasticsearch, publish → becomes searchable, eventual-consistency window,
redelivery-is-a-no-op) was already built during P2.T3, since idempotency and
eventual-consistency verification shared the same test harness — no
duplicate test written. What was still missing, per the phase kickoff's own
task description: unit tests for **`event-service`'s producer** message-
building (payload shape, correct key) and **`search-service`'s repository**
query-building — both added now.

`tests/unit/test_event_producer.py` (`event-service`): mocks the underlying
`AIOKafkaProducer`, asserts `EventProducer.publish_upserted`/
`publish_deleted` send to the right topic, key the message with the raw
event ID bytes (not a JSON-encoded string), and produce the exact JSON
payload shape (`action`, flattened `seats`, etc.) — this is the one thing
the existing `EventManager` tests never actually verified, since they mocked
the producer itself rather than exercising it.

`tests/unit/test_event_index_repository.py` (`search-service`): mocks the
`AsyncElasticsearch` client, asserts `EventIndexRepository.search()` builds
`match_all` for a blank query vs. `multi_match` over the right four fields
for a real one, sorts by `_score` for relevance vs. `start_time` for that
sort field, always appends the `event_id` tiebreaker, and passes pagination
args through as `from_`/`size` correctly (not swapped).

17/17 `event-service` tests green, 17/17 `search-service` tests green.
Combined with every prior task's live verification against the real running
stack (Kafka messages read directly off the topic, real `curl` calls through
Traefik, real Elasticsearch document counts), this closes out Phase 2's
per-task work. Phase-end checklist (walkthrough, report, commit/file scan,
`architecture.html`, decisions-log delta check, CLAUDE.md self-update check,
`/pre-pr` review gate) follows next, before the CHECKPOINT commit.

---

## 2026-08-15 — Phase 2 checkpoint: phase-end checklist

Ran the seven-item phase-end checklist from `CLAUDE.md` against Phase 2
before tagging CHECKPOINT.

**1. End-to-end testing** — no single walkthrough needed; per-task live
verification already covered the full loop repeatedly across P2.T1–T4
(publish → Kafka message read directly off the topic; hand-replayed
duplicate messages; `GET /search` through Traefik against real published
events; pagination/sorting over a real multi-event set). Re-confirmed the
validation checkpoint explicitly one more time as a single pass: create →
publish → appears in search within the consistency window → delete →
disappears → duplicate Kafka delivery changes nothing. All held.

**2. Report writing** — drafted this phase's evidence into five existing
chapter files rather than leaving it only in `build-log.md`:
`class-diagrams.md` (Search Service's Manager+Repository shape, and why a
non-SQL-store Repository doesn't break the pattern), `testing-strategy.md`
(the Kafka+ES `testcontainers` pattern, the eventual-consistency and
redelivery tests as concrete proof rather than claims, and the Docker-disk
incident as a citable "why the integration tier exists" example),
`technologies-used.md` (Kafka's status upgraded from smoke-tested to
Implemented/Tested; new Elasticsearch entry), `requirement-gathering.md`
(the publish-endpoint roles/permissions row, and the §15 amendment
explained in requirements terms), `database-schema-design.md` (the ES
index mapping, alongside the existing Postgres/Mongo schemas). Chapter
status table in `docs/report/README.md` updated to match all five.

**3. Commit history & file scan** — six Phase 2 commits (`aa8ded2..HEAD`
before this checkpoint), each a real, complete unit of work, none
commit-spam. Full diff scanned for emoji, `TODO`/`FIXME`/`XXX`, and casual
language — none found. `LICENSE` still absent, commit author identity
unchanged.

**4. `docs/architecture.html`** — retitled "Event & Search Services",
badges updated (Phase 2 complete, 19 tasks, 10 containers). Topology
diagram (§01) redrawn: `kafka`/`elasticsearch` moved from the idle row into
an active publish → consume → index chain, `search-service` added, Redis
is now the only idle box. New §03 sequence diagram traces the full publish
→ Kafka → consumer → Elasticsearch flow including the eventual-consistency
window and the redelivery/duplicate-delete cases side by side. Both
diagrams rendered and visually checked via a local preview (`python3 -m
http.server` + Playwright screenshots) before committing — caught and fixed
one real overlap (a routing-priority label crossing straight through the
Postgres/MongoDB boxes) rather than shipping it unchecked. "What's proven"
checklist and the reproduce-yourself commands both updated for the new
publish → search flow.

**5. `decisions-log.md` delta check** — one real delta this phase, already
logged inline under §15 at P2.T1 time (the `DRAFT`/`PUBLISHED` two-state
model kept and a dedicated `publish` endpoint added, rather than the
original single-step "creation = publishing" wording) rather than deferred
to this checkpoint. Nothing else this phase changed a locked decision — the
Traefik routing behavior and Kafka topic naming are implementation details
within already-decided architecture, not decisions-log material.

**6. `CLAUDE.md` self-update check** — added two convention notes: how a
service with no SQL/Mongo of its own (Elasticsearch, not a source of
truth) still fits the `db/`-holds-Repositories layering shape, and the new
Traefik routing rule (specific `PathPrefix` per service beyond
`event-service`, verified via Traefik's own router API rather than
assumed).

**7. Review gate** — `/pre-pr` (simplify → code-review → verify) against
the diff from `aa8ded2` (last Phase 1 commit) to this checkpoint follows
next, as its own entry once it completes.
