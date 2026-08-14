# Build Log

Append-only, chronological diary of how this project actually got built — every
session, what was attempted, what failed, what worked, and any decision made
along the way that isn't big enough for `decisions-log.md` (which is locked,
§1–27, and reserved for architectural decisions made *before* implementation).
This file is the opposite: it's *during*-implementation reality, including the
dead ends.

Each entry is dated and tagged with the task it belongs to (per
`phase-0-kickoff.md` / `master-development-plan.md`). Newest entries go at the
bottom.

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
