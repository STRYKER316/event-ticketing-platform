# Phase 7 Kickoff — Frontend (5 screens)

**Goal of this phase:** a minimal functional React UI — login/register,
event list/search, event detail with an interactive polling seat map,
checkout (hold → Stripe test payment) → confirmation, and a minimal
organizer create-event screen — enough to drive the full click-path demo
end to end. Backend stays the graded emphasis (§10); this phase is
deliberately not a polished product build.

**How to use this file:** run the five tasks below **in order**, one per
Claude Code session. Commit after each (small, green commits). `main`
stays bootable at every step. Give Claude Code the repo plus
`decisions-log.md` and `master-development-plan.md` as context.

**Entry deps:** P2 (Event Service, seat maps), P3 (Booking Service, both
hold strategies), P4 (Payment Service, charge flow) all complete and
checkpointed. Per the locked build order (§27), Phase 7 runs after Phase 6
and Phase 5, both already checkpointed.

**Five gaps found and resolved** — the first four before this task list was
finalized, the fifth only surfaced once P7.T1's live verification actually
drove a token through the real frontend client (decisions-log §23 records
the first two, §5 the fifth; gaps 3-4 are local config corrections, not
architecture):

1. **The seat map's live per-seat status has no read endpoint.**
   `booking-service` only exposes `POST /bookings`, `POST /bookings/{id}/pay`,
   `POST /bookings/{id}/cancel` — nothing lets the frontend learn which
   `ticket_id` maps to which seat, or that seat's current status. §23
   already says the frontend composes layout (Event/Mongo) with live status
   (Booking) client-side; the read half of that composition never got a
   route. **Resolved:** a new **public, no-auth** `GET
   /bookings/events/{event_id}/tickets` returning
   `[{ticket_id, section, row_name, seat_label, status, price_cents}]`, one
   row per ticket. Public because browsing seat availability (like
   `event-service`'s own `GET /events*` routes) shouldn't require login —
   only booking itself does. Thin addition: a new
   `TicketRepository.list_by_event` query method plus a route calling
   `BookingManager.list_tickets_for_event` — the same Manager+Repository
   shape every other route in this file uses, not the
   `search-service`-`EventConsumer` Repository-direct exception (that
   exception is for a Kafka consumer with no equivalent API route to
   unify with; this addition *is* the equivalent API route, so it gets
   its own Manager method like the rest).
2. **Keycloak registration is currently disabled.** `registrationAllowed`
   is unset (defaults false) on the `ticketing` realm; only the three
   seeded demo users (`alice`/`bob`/`carol`) exist. P7.T1's "login/register"
   needs a real register flow. **Resolved:** set `registrationAllowed:
   true` in `infra/keycloak/realm-export.json`. New self-registered users
   get no realm role by default (Keycloak's out-of-the-box behavior without
   a configured default-role group) — acceptable, since booking (the only
   user-role-gated action) has no ownership-scoping dependency on the
   `user` role specifically; `require_role` isn't used on any booking
   route, only `get_current_user`. Confirmed by re-reading
   `booking-service/app/api/bookings.py`: no route there calls
   `require_role`. Organizer actions still require the `organizer` role,
   which self-registration correctly does **not** grant — matches "the
   frontend organizer screen is for the seeded organizer accounts
   (`bob`/`carol`), not for a walk-up self-registration."
3. **Traefik routing for the frontend must not touch the locked
   `event-service` catch-all.** `CLAUDE.md` explicitly locks
   `event-service`'s `PathPrefix('/')` as a deliberate, left-as-is fallback
   — "not retrofitted" is called out by name. Giving the frontend that same
   catch-all would mean two competing `PathPrefix('/')` routers with no
   defined winner. **Resolved:** the frontend gets its own specific prefix
   like every other post-`event-service` service is required to —
   `PathPrefix('/app')` — rather than reinterpreting the root path. The SPA
   is mounted at `/app` (React Router `basename="/app"`, nginx `try_files`
   SPA fallback scoped under `/app/`), not at bare `/`. This is a config
   choice, not an architecture change, so no decisions-log delta beyond
   noting the frontend's own row in `infra/README.md`'s router table at
   CHECKPOINT.
4. **Vite's default dev port (5173 is actually Vite's default — an
   earlier assumption of `3000` was wrong) collides with Grafana**, which
   already owns `3000` via `GRAFANA_PORT` in `.env.example`. **Resolved:**
   frontend dev server stays on Vite's real default, `5173`; Keycloak
   client redirect URIs/web origins (`infra/keycloak/realm-export.json`,
   client `ticketing-frontend`) updated to
   `http://localhost:5173/*` (standalone `npm run dev`) and
   `http://localhost/app/*` (full docker-compose stack through Traefik on
   port 80), replacing the stale `http://localhost:3000/*` entry.
5. **`ticketing-frontend` had no audience-mapper, so its tokens carried no
   `aud` claim at all** — found only by live-verifying a real login against
   the real stack (P7.T1's own "done when" bar), not by any earlier
   phase's tests, since none of them drive a token through this specific
   client. Every backend service's `shared_auth` validates
   `AUTH_EXPECTED_AUDIENCE: ticketing-services`; only `ticketing-service`
   (the direct-grant client every prior phase's own tests and manual
   curl checks actually used) carried the `oidc-audience-mapper` that
   stamps that claim on. Every authenticated call from the real frontend
   would have 401'd with "Invalid token" at every service. **Resolved:**
   added the identical protocol mapper to `ticketing-frontend`
   (decisions-log §5 amendment) — re-verified with a real `alice` login
   producing `aud: ticketing-services` and a real `POST /bookings`
   succeeding.

**Process notes specific to this phase (per `CLAUDE.md`):**
- **Build-then-test for everything** — nothing here is on the test-first
  list (dual hold strategies / payment idempotency only). Frontend gets
  Vitest component tests for genuinely non-trivial logic (the seat-map
  layout/status join, the checkout state machine), not coverage of
  presentational components.
- **No dedicated adversarial `/code-review` pass** — only P3 and P8 get
  one. Self-verification (live walkthrough) plus the routine `/pre-pr`
  gate at CHECKPOINT is sufficient here.
- **The new `GET /bookings/events/{event_id}/tickets` route is a Kafka-free,
  DB-read-only addition** — none of the Kafka-consumer conventions
  (manual offset commit, bounded retry) apply to it; it's a plain FastAPI
  route calling a `BookingManager` method, same shape as every other
  route already in the system.
- **Auth split**: real enforcement is server-side only (existing
  `require_role`/ownership checks, unchanged by this phase). The frontend's
  own role check (hiding the organizer screen from non-organizers) is
  client-side UX, not a security boundary — stated explicitly so it's
  never mistaken for one in the report.

---

## P7.T1 — React scaffold + Nginx container behind Traefik; Keycloak login/register

**Prompt to Claude Code:**

First, the backend addition from this file's intro (gap #1) —
`booking-service`: a new `TicketRepository.list_by_event(event_id)` method
(`SELECT` on `Ticket` filtered by `event_id`, no business logic) and a new
public route `GET /bookings/events/{event_id}/tickets` in
`app/api/bookings.py` returning
`list[{ticket_id, section, row_name, seat_label, status, price_cents}]`
(a new `TicketStatusResponse` schema in `app/api/schemas.py`), routed
through a new `BookingManager.list_tickets_for_event` method like every
other route in this file — the route-with-no-Manager shape drafted here
turned out to misapply `search-service`'s `EventConsumer` exception
(that one is for a Kafka consumer with no equivalent API route; this
addition *is* an API route), caught and corrected in the phase's
`/pre-pr` code-review pass. Add a unit test and an integration test (seed a few tickets,
assert the route returns them with correct statuses).

Then scaffold `/frontend` as a Vite + React + TypeScript app (`npm create
vite@latest frontend -- --template react-ts`, then adjust into the
monorepo layout — no nested `.git`). Add:

- `react-router-dom` (routes, `basename="/app"`), `@tanstack/react-query`
  (all API data fetching, including the seat-map poll in P7.T3),
  `react-oidc-context` (+ its `oidc-client-ts` peer dep) for auth.
- An `AuthProvider` configured against Keycloak's OIDC discovery document
  (`${VITE_KEYCLOAK_ISSUER}/.well-known/openid-configuration`), client id
  `ticketing-frontend`, Authorization Code + PKCE (matches the realm
  client's existing `standardFlowEnabled: true` /
  `directAccessGrantsEnabled: false` config — do not add direct-grant
  logic). Login and register both redirect to Keycloak's hosted pages
  (`signinRedirect()` for login; Keycloak's own login page carries a
  "Register" link once `registrationAllowed: true` is set — see the realm
  change below, no separate custom register form needed). Logout via
  `signoutRedirect()`.
- A thin typed `api` client (`frontend/src/api/client.ts`): wraps `fetch`,
  attaches `Authorization: Bearer <token>` from the current OIDC user when
  present, base URLs per backend service read from `import.meta.env`
  (`VITE_EVENT_SERVICE_URL`, `VITE_SEARCH_SERVICE_URL`,
  `VITE_BOOKING_SERVICE_URL`). **Correction from this file's original
  draft**: none of the backend services publish a host port in
  `infra/docker-compose.yml` — only Traefik does (`"80:80"`). So all three
  always resolve to `http://localhost` through Traefik, path-prefixed per
  service (same prefixes `infra/README.md`'s router table documents),
  in both the containerized frontend *and* standalone `npm run dev` — the
  backend stack (`docker compose up`, frontend container excluded) must be
  running either way; there is no direct-port fallback. A 401 response
  triggers `signinRedirect()`.
- Route shell: `/` (redirect to `/search`), `/login` (or a button that
  just calls `signinRedirect` — no separate screen needed since Keycloak
  hosts the actual form), `/search`, `/events/:id`, `/checkout/:ticketId`,
  `/organizer` (protected, role-gated — built out in P7.T5, stub route for
  now), plus a `ProtectedRoute` wrapper redirecting unauthenticated users
  to login.
- `infra/keycloak/realm-export.json`: set `"registrationAllowed": true` on
  the `ticketing` realm; update the `ticketing-frontend` client's
  `redirectUris` to `["http://localhost/app/*", "http://localhost:5173/*"]`
  and `webOrigins` to `["http://localhost", "http://localhost:5173"]`
  (replacing the stale `localhost:3000` entries).
- `frontend/Dockerfile`: multi-stage — `node` build stage (`npm ci && npm
  run build`), `nginx:1.27-alpine` runtime stage serving `/app` (nginx
  `location /app/ { try_files $uri $uri/ /app/index.html; }` SPA fallback,
  static asset root at the Vite build output).
- `infra/docker-compose.yml`: new `frontend` service — `build: context:
  ../frontend`, no host port needed (only reachable through Traefik),
  Traefik labels: `traefik.enable: "true"`,
  `traefik.http.routers.frontend.rule: PathPrefix(\`/app\`)`,
  `traefik.http.services.frontend.loadbalancer.server.port: "80"`.
  `depends_on` isn't needed (static content, no backend dependency at
  container-start time). Build-time env vars (`VITE_*`) baked in via
  Docker build args, matching this phase's config, not injected at
  container runtime (Vite env vars are compile-time).
- `frontend/.env.example` (standalone-dev values: service ports from the
  root `.env.example`, `VITE_KEYCLOAK_ISSUER=http://localhost:8081/realms/ticketing`).

**Done when:** `docker compose up` brings up the `frontend` container
alongside the rest of the stack; `http://localhost/app/` loads the app
shell; clicking login redirects to Keycloak's hosted login page (with a
working Register link) and a successful login/register redirects back with
a valid token visible in the app (e.g. logged via a debug line during this
task, removed before commit — no debug console output stays in committed
code per the Academic-presentation rules).

**Report evidence:** login screenshot.

---

## P7.T2 — Event list/search screen

**Prompt to Claude Code:**

Build the `/search` screen: calls `search-service`'s `GET /search`
(`?q=&limit=&offset=&sort_field=&sort_order=`, `SearchResponse`: `items[]`,
`total`, `limit`, `offset`), via React Query. A debounced text input bound
to `q` (default `""` browses everything, sorted by `start_time` rather than
the default `relevance`, which is meaningless on an empty query) drives
search. Simple pagination (prev/next using `limit`/`offset`, respecting
`search-service`'s own `offset <= 9900` cap) — no infinite scroll, matches
"minimal." Each result links to `/events/:id`. No auth required — this
screen is reachable logged-out.

**Done when:** typing a query narrows results against a real running
`search-service` (seeded with at least one published event from an earlier
phase's demo data, or seed one now if none exists); pagination moves
between pages of results.

**Report evidence:** screenshot.

---

## P7.T3 — Event detail + interactive seat map

**Prompt to Claude Code:**

Build `/events/:id`: `GET /events/{id}` (event-service, title/description/
times/venue/performers) rendered as a header, plus the seat map body
composed from two calls:

- `GET /events/{id}/seat-map` (event-service) — layout: sections → rows →
  seats (`label`, `x`, `y`), fetched once (React Query, no polling —
  layout is static per §23).
- `GET /bookings/events/{id}/tickets` (booking-service, the new P7.T1-scoped
  addition) — live status per ticket, **polled** via React Query's
  `refetchInterval` (5000ms) per §23's polling-not-push decision.

Join client-side on `(section, row_name, seat_label)` ↔ layout's
`(section.name, row.name, seat.label)`. Render seats positioned by
`(x, y)` (an SVG or absolutely-positioned-div grid — implementer's choice,
keep it simple, no seat-map charting library needed for this scope), color
by status (available / held / booked), disable click on non-available
seats. Clicking an available seat navigates to `/checkout/:ticketId`
(ticketId = that seat's `ticket_id` from the joined data), or prompts login
first if unauthenticated (booking requires auth; browsing the map does
not).

**Done when:** the seat map renders real layout+status against a real
running stack; holding a seat in one browser tab (or via a direct `curl
POST /bookings`) visibly flips that seat's color in another tab/window
within one polling interval, without a manual page refresh.

**Report evidence:** seat-map figure (screenshot showing at least one
available, one held, one booked seat).

---

## P7.T4 — Checkout + confirmation

**Prompt to Claude Code:**

Build `/checkout/:ticketId`: on mount, calls `POST /bookings`
(`{ticket_id}`) to acquire the hold, producing a `BookingResponse`
(`PENDING`). Show the held seat + its price (from the join done in P7.T3 —
pass through via route state, or refetch the single ticket's row from the
same tickets-by-event endpoint if navigated to directly). A "Pay" action
calls `POST /bookings/{id}/pay`, which fronts the synchronous
Booking→Payment call (§9 amendment) and returns `BookingPayResponse`
(`payment_id`, `status`, `amount_cents`, `currency`) — no real card form,
per §9's fixed-test-payment-method scope (nothing to build here beyond the
button triggering the call). On success, navigate to `/confirmation` with
the `BookingPayResponse` in route state and render it there.

**Known, documented limitation:** there is no `GET /bookings/{id}` route,
so `/confirmation` is a post-payment render only — a hard refresh loses the
confirmation detail (the booking itself is unaffected; it's already
`CONFIRMED` server-side). Acceptable for this phase's minimal scope; note
it explicitly in `decisions-log.md` §26's limitations pull-list at
CHECKPOINT rather than building a new GET-by-id endpoint to cover a
refresh case the 5-screen scope doesn't call for.

Handle the hold-acquisition failure path (409 if the seat was taken
between the map rendering and the click — a real race under concurrent
users) with a clear error and a way back to the seat map, not a silent
failure.

**Done when:** a full hold → pay → confirmation click-path completes
end-to-end against the real running stack with a real (test-mode) Stripe
charge; the 409 race case is reproduced once (two tabs racing the same
seat) and shown to fail cleanly in the losing tab.

**Report evidence:** checkout + confirmation screenshots.

---

## P7.T5 — Minimal organizer view

**Prompt to Claude Code:**

Build out the `/organizer` route stubbed in P7.T1: role-gated (hide/redirect
if the logged-in user's token has no `organizer` realm role — decode via
`react-oidc-context`'s user profile/access token claims, client-side only,
not a security boundary per this file's intro). A single-page flow, not
five separate screens:

1. Create venue (`POST /venues`: name, address, capacity).
2. Create event (`POST /events`: title, description, start/end time, venue
   id, performer ids). **Checked:** `event-service` has no
   performer-create or performer-list route at all — performers only ever
   enter the system via `app/seed.py`, and `EventCreate.performer_ids`
   404s on any id it can't resolve. This is a pre-existing gap, not
   something P7 introduces or needs to fix; submit `performer_ids: []`
   from this form (an event with no listed performers is valid — the
   field's own default is `[]`) rather than building UI for an API that
   doesn't exist.
3. Set seat map (`PUT /events/{id}/seat-map`) — a structured form (section
   name, row name, seat labels, price per section), not a raw JSON
   textarea and not a drag-and-drop builder — sections/rows/seats added via
   simple repeated-field inputs.
4. Publish (`POST /events/{id}/publish`).

Each step's response feeds the next (venue id → event; event id → seat
map/publish). Surface backend validation errors (422s) inline, not as a
generic failure.

**Done when:** logging in as a seeded organizer account (`bob` or `carol`),
a full venue → event → seat map → publish flow completes and the new
event is immediately visible on `/search` and bookable end-to-end (ties
back into P7.T2–T4). Logging in as `alice` (no organizer role) does not
show this screen.

**Report evidence:** — (per the master plan, this task has none listed;
none needed).

---

## Phase 7 exit checklist (all must pass before P8/P9)

- [ ] End-to-end live walkthrough: login/register → search → seat map
      (with a live cross-tab status update observed) → checkout → pay →
      confirmation, all against the real docker-compose stack, not mocked.
- [ ] Organizer flow live-verified: venue → event → seat map → publish →
      event appears in search → bookable.
- [ ] The two documented gaps from this file's intro are live-verified as
      fixed: `GET /bookings/events/{event_id}/tickets` returns real data
      against a seeded event; Keycloak registration produces a working new
      login.
- [x] Vitest suite green for the seat-map join/polling logic and the
      checkout state machine. Evidence: 8/8 passing, confirmed repeatedly
      across all three `/pre-pr` code-review rounds (2026-08-20).
- [x] `docs/architecture.html` updated: frontend container in the topology
      diagram, the `/app` Traefik route, current proven/not-built lists.
      Evidence: commit `e5bf8a3`.
- [x] `decisions-log.md` §23 amendment added (the new booking-service
      endpoint) and §26 limitations pull-list extended (no
      confirmation-page refresh persistence). Evidence: §23 amendment plus
      a same-session correction paragraph after the `BookingManager`
      routing fix; §26 line added this session (`ConfirmationPage.tsx`'s
      own code comment referenced this decisions-log entry before it
      actually existed — now it does).
- [x] `CLAUDE.md` self-update check done (frontend conventions, if any
      emerged worth locking in — e.g. the `/app` Traefik mount pattern for
      future non-API services, if applicable). Evidence: explicitly
      checked this session — no addition made, since Phase 7 is this
      project's one and only frontend phase (no further screens or
      non-API services are planned in the locked build order), so none of
      this phase's frontend-specific patterns (StrictMode double-invoke
      guards, fail-loud `VITE_*`/OIDC-issuer startup checks) have a future
      call site in this repo to generalize for.
- [x] Cross-doc staleness sweep: root `README.md`, `infra/README.md`
      (router table gets a `frontend` row; `.env.example` port docs), any
      report chapter referencing "5 screens" as still-planned rather than
      built. Evidence: root `README.md`'s Layout/Local-Development/Status
      sections and `infra/README.md`'s intro paragraph + ports table
      updated this session (both were stale — `infra/README.md`'s intro
      was already missing `notification-service` from Phase 5, not just
      `frontend`); `docs/report/` already correctly describes Phase 7 as
      built (checked via grep for stale "5 screens" planning language —
      none found) and its chapter-status table already lists P7 evidence.
- [ ] `/pre-pr` run against the diff since `2f583b8` (the commit this phase
      started from); findings triaged and real ones fixed. Step 1
      (simplify) and Step 2 (code-review, three rounds — 11, 3, then 1
      finding) complete and clean; Step 3 (verify) in progress.
- [x] `docs/build-log.md` entries appended per task plus the CHECKPOINT
      entry. Evidence: entries at 2026-08-20 for kickoff/T1-backend,
      T1-frontend, T4 race-testing, and the two `/pre-pr` gate rounds.
