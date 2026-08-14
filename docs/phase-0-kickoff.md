# Phase 0 Kickoff — Foundation & Walking Skeleton

**Goal of this phase:** prove the plumbing before any business logic. By the end, an authenticated request routes Traefik → a FastAPI service → validates a real Keycloak JWT → hits a DB → returns, with structured JSON logs and `/metrics`; Kafka round-trips; the whole stack boots on the M3 (decisions-log §24).

**How to use this file:** run the seven tasks below **in order**, one per Claude Code session. Commit after each (small, green commits). `main` stays bootable at every step — if a session ends mid-task, the previous commit still `docker compose up`s cleanly. Give Claude Code the repo plus `decisions-log.md` and `master-development-plan.md` as context so it stays anchored to the locked decisions (referenced by § below).

---

## Prerequisites (check before P0.T1)

- [x] **Docker Desktop** installed and running; Settings → Resources → memory set to ~8–12 GB (§24). Already installed per §24 — just confirm the memory allocation.
- [x] **Git** + a new empty **GitHub repo** (private is fine) to push to.
- [ ] **Python 3.12** available locally (`uv` recommended for speed, or plain `pip` + venv — your call).
- [x] **Claude Code** installed and authenticated.
- [x] Copy `decisions-log.md` and `master-development-plan.md` into the repo's `/docs` folder (P0.T1 creates the folder).
- [ ] AWS / Stripe / SendGrid — **not needed yet.** AWS is P10; Stripe CLI is P4; notifications are log-only (§19).

---

## P0.T1 — Monorepo skeleton
**Prompt to Claude Code:**
> Create a monorepo skeleton matching decisions-log §20. Top-level folders: `/services` containing `event-service/`, `search-service/`, `booking-service/`, `payment-service/`, `notification-service/` (each with an empty `app/` and a placeholder `README.md`); `/frontend`; `/infra`; `/docs`. Add a root `README.md` describing the project and the folder layout, a root `.gitignore` covering Python (`__pycache__`, `.venv`, `*.pyc`, `.env`) and Node (`node_modules`, build output), and a root `.env.example` with placeholder keys for DB creds, Keycloak, and service ports (no real secrets). Initialize git and make the first commit. Do not add service code yet — this is structure only.

**Done when:** `tree` matches the §20 layout; repo pushed to GitHub; first commit exists. ✅ Done

---

## P0.T2 — Infra docker-compose
**Prompt to Claude Code:**
> In `/infra/docker-compose.yml`, define the shared infrastructure (no app services yet). All images must be arm64-native (§24). Include:
> - **Postgres** — a single container (§12) with an init script (`/infra/postgres/init.sql`) that creates three databases `event_db`, `booking_db`, `payment_db`, each with its own user/password, for per-service credential isolation. Add a healthcheck.
> - **MongoDB**, **Redis** — standard single-container setups with healthchecks.
> - **Elasticsearch** — `discovery.type=single-node`, `ES_JAVA_OPTS=-Xms512m -Xmx512m` (§24), security disabled for local dev, healthcheck on the cluster-health endpoint.
> - **Kafka** — Apache's official image in **KRaft mode**, no Zookeeper (§7, §24). Single broker, healthcheck.
> - **Traefik v3** — Docker provider (labels-based discovery), dashboard enabled on a local port, entrypoint on `:80`.
> Pull all credentials/ports from a `.env` file (update `.env.example` accordingly). Add a short `/infra/README.md` documenting each service's port.

**Done when:** `docker compose up` brings every infra container to healthy; `docker compose ps` shows all green; the Traefik dashboard loads. ✅ Done

---

## P0.T3 — Keycloak + realm
**Prompt to Claude Code:**
> Add **Keycloak** to `/infra/docker-compose.yml` in **dev mode** (`start-dev --import-realm`) with its embedded database (§5, §12) — no dedicated Postgres. Create a realm export at `/infra/keycloak/realm-export.json` defining: a realm (e.g. `ticketing`); two realm roles `user` and `organizer` (§15); one client for the frontend (public, PKCE) and one confidential/direct-access client usable for obtaining test tokens via password grant; and 2–3 seed users (one plain `user`, one with both `user` and `organizer` roles). Mount the export so it imports on startup.

**Done when:** Keycloak boots and imports the realm; a token can be obtained via password grant for a seed user, and its decoded claims show the expected realm roles.

---

## P0.T4 — Shared auth dependency *(the high-leverage one — §3)*
**Prompt to Claude Code:**
> Create a small shared Python package (e.g. `/services/_shared/auth/`) providing a reusable FastAPI dependency for Keycloak JWT validation, since every service validates tokens independently (§4). It must: fetch the realm's JWKS from Keycloak and cache it (refresh on unknown `kid`); validate the token's RS256 signature, `iss`, `aud`, and expiry; expose `get_current_user()` returning the decoded principal (subject + roles from `realm_access`); and provide a `require_role("organizer")`-style dependency factory that 403s when the role is absent (§15). Write pytest unit tests covering: valid token passes; expired/tampered token rejected; missing required role → 403. Mock the JWKS endpoint in tests so they don't need a live Keycloak.

**Done when:** unit tests green; the dependency is importable by any service.

---

## P0.T5 — Service template + walking skeleton
**Prompt to Claude Code:**
> Build a minimal FastAPI service template and instantiate it once (call it `event-service` for now, but keep the app factory reusable across services). It must include: a `/healthz` endpoint; a `/metrics` endpoint via `prometheus-fastapi-instrumentator`; structured JSON logging via `structlog`; a `Dockerfile` (`python:3.12-slim`, uvicorn); and one **protected** demo route using the P0.T4 auth dependency plus one `organizer`-only route. Add the service to `/infra/docker-compose.yml` behind Traefik using Docker labels for routing. Connect it to `event_db` (a trivial "SELECT 1" health-check query is enough for now — no schema yet).

**Done when:** `curl` through Traefik to `/healthz` returns 200; the protected route returns 401 without a token and 200 with a valid Keycloak token; the organizer-only route 403s for a plain `user`; logs come out as JSON; `/metrics` scrapes.

**This is the walking-skeleton validation checkpoint** — the whole point of Phase 0.

---

## P0.T6 — Kafka smoke test
**Prompt to Claude Code:**
> Write a throwaway **aiokafka** producer + consumer (a script under `/infra/` or a pytest) that publishes a message to a test topic on the compose Kafka broker and consumes it back, proving the broker works end-to-end before real integrations depend on it (§7). Keep it isolated — it's a smoke test, not wiring for any service.

**Done when:** the message published is consumed and asserted equal; runs green against the running compose stack.

---

## P0.T7 — Dev workflow
**Prompt to Claude Code:**
> Add developer ergonomics: a root `Makefile` (or `justfile`) with `up`, `down`, `logs`, `test` targets (§25); a small `get-token.sh` helper that does the Keycloak password-grant curl and prints an access token for manual API calls; and a "Local Development" section in the root README covering how to boot the stack, get a token, hit a protected endpoint, and a placeholder note that the **Stripe CLI** (`stripe listen --forward-to ...`) will be needed later for P4 (§24).

**Done when:** `make up` / `make test` work from a clean checkout; `get-token.sh` returns a usable token.

---

## Phase 0 exit checklist (all must pass before P1)

- [ ] Full infra stack boots healthy on the M3 in one `make up`.
- [ ] Authenticated request round-trips Traefik → service → Keycloak-validated → DB → 200.
- [ ] Role enforcement works (`user` vs `organizer`).
- [ ] Structured JSON logs + `/metrics` on the service.
- [ ] Kafka produce→consume proven.
- [ ] Everything committed; `main` bootable.

**Report evidence captured this phase (§16):** local topology figure (v1), auth-flow figure, "auth as a platform dependency" writeup, the service-scaffold pattern — these feed the *Project Description*, *Requirement Gathering*, and *Technologies Used* drafts (Milestone A).

**Next:** Phase 1 — Event Service (source of truth). Generate its task prompts the same way once Phase 0's exit checklist is green.
