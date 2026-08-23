# Phase 10 Kickoff — AWS Elastic Beanstalk Deployment

**Goal of this phase:** prove the stack runs on real cloud infrastructure and
host the graded demo (master plan Phase 10). Local-first (§25) means this is
a late, thin deployment checkpoint, not where day-to-day development
happens — everything up to this point has been built and tested entirely
against `docker compose` locally. This phase's job is narrow: get the
already-working stack running on Elastic Beanstalk, prove the main flow
works there too, and document the cost/operational discipline around it. No
new application logic, no schema changes, no new Kafka points.

**How to use this file:** run the four tasks below in order, one per Claude
Code session where practical. Commit after each (small, green commits).
`main` stays bootable at every step. Unlike every phase before it, this
phase creates real, billed AWS resources — see "Process note" below before
running any task that provisions or deploys anything.

**Entry deps:** P9 (Hardening) — confirmed complete:
`docs/phases/phase-9-kickoff.md`'s exit checklist is fully checked off with
evidence, all 5 suites green (186/186 tests), CHECKPOINT `/pre-pr` and
cross-doc staleness sweep both run.

**Pre-read audit (done as part of writing this kickoff doc, not deferred to
P10.T1 itself):**

- **P10.T1's scope is larger than "deploy the compose file" suggests.** No
  AWS/EB-specific files exist anywhere in the repo yet (`.ebextensions/`,
  `Dockerrun.aws.json`, or similar) — this is genuine build-from-scratch,
  not a consolidation pass like most other phases' pre-read findings. More
  importantly, a real gap was found that would silently fail P10.T3's
  validation checkpoint if not caught here first: the frontend's Vite build
  bakes `VITE_EVENT_SERVICE_URL`/`VITE_SEARCH_SERVICE_URL`/
  `VITE_BOOKING_SERVICE_URL` and `VITE_KEYCLOAK_ISSUER` as
  `http://localhost...` **build-time** constants
  (`infra/docker-compose.yml`'s `frontend.build.args`), and Keycloak's own
  realm config (`infra/keycloak/realm-export.json`) locks
  `redirectUris`/`webOrigins` to `http://localhost/app/*` and
  `http://localhost:5173/*`. None of this resolves once the app is reached
  via the EB environment's public CNAME instead of a developer's own
  machine — login and every API call from the deployed frontend would
  break, even though "the containers start" would technically be true. This
  has to be fixed as part of T1, not discovered during T3.
- **The container count is smaller than §12's "14-15" ceiling for a normal
  deploy.** `prometheus`/`grafana` are `profiles: ["benchmark"]`-gated (only
  started via `make bench-up`, per P8) — they won't run during T1's baseline
  EB deployment or T3's validation smoke test, only if a benchmark run is
  deliberately repeated against the deployed instance later. 16 named
  services total in the compose file (6 are volume declarations, not
  containers) confirms §12's estimate for the full stack including
  benchmark tooling; the actual always-on footprint for T1-T3 is smaller.
- **Inter-service networking is already EB-portable.** Every
  backend-to-backend call in `docker-compose.yml` already uses the Docker
  network's service name (`postgres`, `kafka:9092`, `redis`,
  `elasticsearch`, `mongodb`, `keycloak:8080`), not `localhost` — so §12's
  "deploy the existing docker-compose.yml directly" claim holds for the
  backend services. Only the frontend's build-time public-facing URLs and
  Keycloak's client config (bullet above) need environment-specific values.
- **The secrets/config set for P10.T2 is concretely enumerable from
  `.env.example`** (66 lines): 7 database credential pairs (admin + 3× per-
  service user/password), Keycloak admin password + client secret, and the
  2 Stripe test-mode keys — roughly 10 real secret values need to move from
  a local `.env` (gitignored, never committed) to EB environment properties
  (§19 already decided this is where secrets/config live for AWS, no
  separate secrets manager). Everything else in `.env.example` (hosts,
  ports, non-secret names) can stay as EB environment properties too or be
  baked into `.ebextensions`, either is fine — only the ~10 actual secrets
  need careful handling.
- **Keycloak is reached directly, not through Traefik.**
  `infra/docker-compose.yml`'s `keycloak` service has no `traefik.*`
  labels — it's exposed via its own published host port
  (`${KEYCLOAK_PORT}:8080`, default 8081), separately from Traefik's port
  80. This means the EB security group needs **two** public inbound rules
  (80 for Traefik/the app, and the Keycloak port for the OIDC login
  redirect), not just one — easy to miss if the security-group task is
  scoped only around Traefik.

**Open item requiring the user, not a design decision:** every prior phase
ran entirely against local Docker — this phase is the first that needs an
actual AWS account with billing enabled, and either the AWS CLI or the `eb`
CLI configured with real IAM credentials on whatever machine runs the
deploy. Nothing in `decisions-log.md` or `master-development-plan.md`
addresses account/credential setup (checked, not present) because it was
out of scope for every phase before now. Claude Code cannot provision AWS
account access on its own — confirm before P10.T1 starts that an AWS
account and IAM credentials (with EB/EC2/S3 permissions) are ready, or this
phase blocks on that first.

**Process note specific to this phase:** every task in this phase either
provisions billed AWS resources or runs commands against them (`eb create`,
`eb deploy`, security-group changes, the Budgets alert). Per this
assistant's standing operating rule on hard-to-reverse or cost-incurring
actions affecting infrastructure outside the local machine, each such step
gets an explicit confirm-before-running check with the user in the
moment — this file lays out the plan, it doesn't pre-authorize the AWS
actions themselves. No dedicated adversarial `/code-review` pass is
required here (reserved for P3/P8 only, per CLAUDE.md) — the routine
`/pre-pr` gate at CHECKPOINT plus self-verification is sufficient, same as
every phase besides P3/P8.

---

## P10.T1 — EB environment + fix hardcoded-localhost URLs

**Prompt to Claude Code:**
> Two things, in order. First, fix the pre-read audit's real gap: make the
> frontend's build-time URLs (`VITE_EVENT_SERVICE_URL`,
> `VITE_SEARCH_SERVICE_URL`, `VITE_BOOKING_SERVICE_URL`,
> `VITE_KEYCLOAK_ISSUER` in `infra/docker-compose.yml`'s `frontend.build.args`)
> and Keycloak's `redirectUris`/`webOrigins`
> (`infra/keycloak/realm-export.json`) work against whatever public address
> the app is actually reached at, not a hardcoded `localhost`. Prefer
> relative/same-origin URLs for the three service URLs if that's a clean fit
> (Traefik already serves the frontend and proxies the APIs from the same
> origin/port), and make the Keycloak issuer and realm redirect/origin
> config driven by an environment value rather than a literal string, so the
> same image works locally and on EB without a rebuild. Verify locally
> first — full login-through-API round trip still works against
> `docker compose up` before touching AWS. Second, create the EB application
> and environment: Docker platform branch (AL2023), single-instance (no load
> balancer), t3.xlarge, deploying the existing `docker-compose.yml`. Confirm
> with the user before running any command that actually provisions AWS
> resources.

**Done when:** the stack runs on EB and the frontend's login/API calls
resolve correctly against the real EB address (not just "containers
started" — a login attempt through the deployed frontend must actually
complete). Report evidence: Deployment Flow chapter.

---

## P10.T2 — Environment properties, security groups, Budgets alert

**Prompt to Claude Code:**
> Move the ~10 real secrets identified in the pre-read audit (7 DB
> credential values, Keycloak admin password + client secret, 2 Stripe
> test-mode keys) from the local `.env` into EB environment properties —
> never commit them. Configure the EB environment's security group with two
> public inbound rules: port 80 (Traefik/the app) and the Keycloak port (the
> OIDC login redirect needs to reach Keycloak directly, per the pre-read
> finding that it isn't proxied through Traefik). Set an AWS Budgets alert
> at ~$20 (§13) as a safety net. Confirm with the user before applying
> security-group changes or creating billing alerts, same as T1.

**Done when:** the deployed environment runs on EB-managed environment
properties with no secret committed to the repo, the security group opens
exactly the two ports that need to be public, and the Budgets alert is
live. Report evidence: Deployment figures.

---

## P10.T3 — AWS validation run

**Prompt to Claude Code:**
> Smoke-test the main flow end-to-end against the deployed EB environment,
> not local Docker: browse events, book a seat, pay (Stripe test mode), and
> confirm the booking, using a real browser session against the EB CNAME.
> Capture screenshots of each step for the report. If anything breaks that
> T1's local verification didn't catch (a residual localhost-only
> assumption, a security-group gap, a missing environment property), fix it
> and re-run the full flow before calling this done — don't partially
> validate and move on.

**Done when:** browse→book→pay→confirm works on the deployed EB
environment, verified live, with screenshots captured. Report evidence:
"Deployed" screenshots.

---

## P10.T4 — Stop/terminate discipline + CNAME stability writeup

**Prompt to Claude Code:**
> Stop (not terminate) the EB environment's instance once T3's validation is
> done, per §13's decided stop-vs-terminate reasoning (small dollar
> difference, terminate risks image re-pull and platform drift, stopping
> keeps the environment CNAME stable). Document in the Deployment Flow
> report chapter: the actual stop procedure used, confirmation the CNAME is
> still the same after a stop/restart cycle (verify this live, don't just
> cite the decision), and the real cost incurred so far against §13's ~$3-6
> estimate. Note explicitly that full termination is deferred until the
> project is submitted and graded (§13), not done now.

**Done when:** the instance is stopped, CNAME stability is live-verified
(not just asserted from the decision), and the cost writeup reflects real
AWS billing console numbers, not an estimate. Report evidence: Cost
writeup.

---

## Phase 10 exit checklist (all must pass before P11 resumes)

- [ ] P10.T1 — hardcoded-localhost gap fixed and verified locally; EB
      app/environment created (Docker platform branch, AL2023,
      single-instance, t3.xlarge); stack running with a working login+API
      round trip against the real EB address.
- [ ] P10.T2 — secrets moved to EB environment properties (none committed);
      security group opens exactly ports 80 and the Keycloak port; AWS
      Budgets alert live at ~$20.
- [ ] P10.T3 — browse→book→pay→confirm live-verified against the deployed
      EB environment; screenshots captured.
- [ ] P10.T4 — instance stopped; CNAME stability live-verified across the
      stop/restart; real cost writeup against the §13 estimate.
- [ ] Full suite green (unit + integration) across all 5 services — confirm
      nothing in this phase's changes (the frontend/Keycloak URL fix) broke
      local `docker compose up` or existing tests.
- [ ] Live walkthrough done at CHECKPOINT — this phase's walkthrough is
      inherently live (it's the AWS validation run itself, P10.T3) rather
      than a separate step.
- [ ] `docs/architecture.html` updated to current state — deployment
      topology reflects the real EB environment, not just local compose.
- [ ] `decisions-log.md` delta check — explicitly confirm whether the
      frontend/Keycloak URL-configurability fix (a genuine
      implementation-level extension of §12, similar in shape to the §22
      amendment's pattern) needs recording as an amendment.
- [ ] `CLAUDE.md` self-update check — explicitly checked, not assumed.
- [ ] Phase-end checklist item 7 (`/pre-pr`) run against the diff since
      this phase's starting commit.
- [ ] Phase-end checklist item 8 (cross-doc staleness sweep) run against
      what this phase's diff actually touched.
- [ ] This file's own exit checklist checked off with evidence notes, per
      CLAUDE.md's phase-end checklist item 9.

**Report evidence captured this phase (§16):** Deployment Flow chapter
(topology, environment properties, security groups, cost writeup,
screenshots).

**Next:** Phase 11 — Report Assembly & Demo resumes, unblocked (per
`docs/phases/phase-11-kickoff.md`'s "Blocked on P10" section and its still-
unchecked "Phase 11 is NOT complete" box).
