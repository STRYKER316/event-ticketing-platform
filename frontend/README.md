# frontend

Minimal functional React UI (§10) — five screens: browse/search, event detail with an
interactive polling seat map, checkout (hold → Stripe test payment), confirmation, and a
minimal organizer create-event flow. Not a polished product build; backend remains the
graded emphasis.

Vite + React + TypeScript, `react-router-dom`, `@tanstack/react-query` (all data
fetching, including the seat-map poll), `react-oidc-context` (Authorization Code + PKCE
against Keycloak's `ticketing-frontend` client). Built as static assets, served by
`nginx:1.27-alpine` behind Traefik at `PathPrefix('/app')` (decisions-log §23
amendment — `event-service` keeps its own locked `PathPrefix('/')` catch-all
untouched).

## Run locally

Backend stack must already be up (`docker compose up` from `/infra`, frontend container
excluded is fine) — none of the backend services publish their own host port, only
Traefik does, so every API call resolves through it regardless of how the frontend
itself is served.

```sh
cd frontend
cp .env.example .env
npm install
npm run dev   # http://localhost:5173
```

Or as part of the full stack: `docker compose up frontend` from `/infra` —
`http://localhost/app/`.

## Test

```sh
npm run test   # Vitest — seat-map join, checkout state machine, seat-label formatting, SHA-256 polyfill
npm run lint   # oxlint
npm run build  # tsc -b && vite build
```

`npm run test` needs Node >=22.12 (see `engines` in `package.json`) — its
jsdom environment pulls in a dependency that only resolves cleanly once
`require(esm)` is on by default. Verified failing on 22.7.0, passing on
26.1.0. `npm run build` and `npm run dev` have no such floor; the
Dockerfile's `node:22-alpine` build stage never runs the test suite.
