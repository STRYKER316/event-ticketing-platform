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
