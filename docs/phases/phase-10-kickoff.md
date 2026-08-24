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
phase creates real, billed AWS resources — see "Credential & execution
model" and "Process note" below before running any task that provisions or
deploys anything.

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

**Credential & execution model for this phase:**

Every prior phase ran entirely against local Docker with no external
account involved. This is the first phase where Claude Code operates
against a real, billed AWS account, so the execution model needs to be
explicit rather than assumed. Two distinct things are going on here, worth
not conflating:

- **Claude Code's own tool-permission prompts are automatic and need no
  setup.** By default it stops and asks for approval before running
  state-changing bash commands — `eb create`, `aws ec2 stop-instances`,
  security-group changes, and so on. This is what the Process note's
  "confirm before running any command that provisions AWS resources" relies
  on; it's Claude Code's normal built-in behavior, not something this phase
  has to configure.
- **AWS credentials actually existing is a separate thing, and is *not*
  prompted for.** If `aws configure` hasn't been run, an `aws`/`eb` command
  doesn't pop an approval dialog — it just fails outright ("Unable to
  locate credentials" or similar). Claude Code will surface that error and
  say what's missing, but it cannot create the IAM user, attach a policy,
  or generate access keys itself — that requires AWS console access it
  doesn't have.

**Once credentials exist, Claude Code drives the AWS side directly, the
same way it drives everything else in this repo** — `eb init`, `eb create`,
`eb deploy`, `eb setenv`, `aws ec2 stop-instances`/`start-instances`,
`aws budgets create-budget`, security-group rule changes, and the T3
smoke-test requests, exactly as a human would type them from the same
terminal. This is plain CLI tool use, not a new integration.

**Credentials for this project: settled, not still pending.** An IAM user
(not root) with the `AdministratorAccess` managed policy is already
provisioned — this is what Claude Code authenticates as for every task
below. Two things worth being explicit about, both already weighed and
accepted, not open questions:
- **IAM user, not root, is the part that actually matters.** Root
  credentials can't be scoped by any policy and their leak radius is the
  whole account including billing/closure; this IAM user's leak radius is
  large (full admin) but the identity itself can be deactivated or deleted
  in seconds and can't do root-only things. That distinction is the real
  safety boundary here, not the exact policy attached.
- **Full `AdministratorAccess` is broader than this phase strictly needs**
  (the minimum would be `AdministratorAccess-AWSElasticBeanstalk` + a
  narrow Budgets permission) — a deliberate, accepted trade-off for a solo
  capstone rather than an oversight. It means one less thing to debug if a
  permission gap shows up mid-task, at the cost of a bigger blast radius if
  the key ever leaked. Mitigate the trade-off with plain key hygiene:
  never paste the access key/secret into a chat message or commit it to
  the repo, and deactivate or delete the key once the project is submitted
  and graded (matching §13's termination timing) rather than leaving it
  live indefinitely.
- Before treating any T1 result as real, confirm the credential actually
  resolves (`aws sts get-caller-identity` returns the right account) — a
  command that silently no-ops or errors shouldn't be mistaken for one
  that succeeded.
- **Cost baseline this phase is measured against** (verified current as of
  this kickoff, 2026-08-24): t3.xlarge on-demand is $0.1664/hr in
  us-east-1, unchanged from §13's original estimate. At §13's projected
  ~20–35 hrs of total EC2 runtime across the whole project, compute cost is
  ~$3–6, ~$7–12 all-in with EBS storage — comfortably inside free-tier
  credit on any account age. T4's real-cost writeup should be reconciled
  against this baseline, not treated as a fresh estimate.

**Two deviations from the above, decided at the start of the actual T1
session (2026-08-24), recorded here rather than only in `build-log.md`:**
- **Auth flow used: `aws login` (AWS CLI v2.32+), not `aws configure`.**
  This section originally assumed the long-lived IAM user access
  key/secret entered once via `aws configure`. Instead, `aws login` was
  used — it reuses an AWS Console sign-in (as the same `AdministratorAccess`
  IAM user, not root) to mint short-lived, auto-refreshing temporary
  credentials, with no static key ever written to disk. Verified via `aws
  sts get-caller-identity` → `arn:aws:iam::427597698460:user/anshilM`, and
  `AdministratorAccess` confirmed attached via the `admin` IAM group (not
  directly on the user, but the same effective permission). Strictly safer
  than the plan as originally written, not a downgrade — noted as a
  deviation only because the credential mechanism differs from what's
  described above.
- **Region: `ap-south-1` (Mumbai), not `us-east-1`.** A deliberate user
  choice, not a default. This directly invalidates the "Cost baseline"
  bullet just above, which is a `us-east-1` on-demand price
  ($0.1664/hr) — `ap-south-1` t3.xlarge pricing is different (typically
  somewhat higher). T4's real-cost writeup must reconcile against actual
  `ap-south-1` billing-console numbers, not the `us-east-1` figure quoted
  above; that figure is left as originally written (it's what was "verified
  current as of this kickoff") rather than edited, so the reconciliation at
  T4 has an honest before/after to show its work against.

**Process note specific to this phase:** every task in this phase either
provisions billed AWS resources or runs commands against them (`eb create`,
`eb deploy`, security-group changes, the Budgets alert). Per this
assistant's standing operating rule on hard-to-reverse or cost-incurring
actions affecting infrastructure outside the local machine, each such step
gets an explicit confirm-before-running check with the user in the
moment — this file lays out the plan, it doesn't pre-authorize the AWS
actions themselves. Additionally, because these four tasks may run across
separate Claude Code sessions rather than one sitting ("one per session
where practical"), **any session that leaves the EB instance running at
its end should explicitly stop it before ending** — this isn't deferred
solely to T4, which documents the *final* stop after T3's validation. An
instance idling between, say, a T1 session today and a T2 session two days
later is exactly the case §13's stop/terminate discipline exists to
prevent. No dedicated adversarial `/code-review` pass is required here
(reserved for P3/P8 only, per CLAUDE.md) — the routine `/pre-pr` gate at
CHECKPOINT plus self-verification is sufficient, same as every phase
besides P3/P8.

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
> `docker compose up` before touching AWS. Second, confirm AWS credentials
> are configured (`aws sts get-caller-identity`) and create the EB
> application and environment: Docker platform branch (AL2023),
> single-instance (no load balancer), t3.xlarge, deploying the existing
> `docker-compose.yml`. Confirm with the user before running any command
> that actually provisions AWS resources. If the session is ending with the
> instance still running, stop it before finishing.

**Done when:** the stack runs on EB and the frontend's login/API calls
resolve correctly against the real EB address (not just "containers
started" — a login attempt through the deployed frontend must actually
complete). Report evidence: Deployment Flow chapter.

**Done (2026-08-24).** Two real gaps found and fixed beyond the pre-read
audit's scope, both live-verified against
`http://event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`:
- EB's Docker-Compose deploy needs `docker-compose.yml` at the deployment
  bundle's root with every `build.context` nested underneath it (confirmed
  against AWS's own docker-compose quickstart) — `infra/docker-compose.yml`'s
  contexts reach one level above `infra/` (`../services`, `../frontend`),
  which doesn't resolve as-is. Fixed with `infra/build-eb-bundle.sh`, which
  assembles a flat, self-contained bundle without touching the canonical
  compose file used for local dev.
- PKCE's `code_challenge` needs `crypto.subtle`, which browsers restrict to
  secure contexts (HTTPS, or a `localhost` hostname specifically) — worked
  in every local test, failed silently against the EB CNAME's plain-HTTP
  real hostname. Fixed with a pure-JS SHA-256 fallback
  (`frontend/src/auth/subtleCryptoPolyfill.ts`), keeping PKCE at full S256
  strength rather than downgrading to `plain`.
- EB has no host-side Python/uv, so a fresh environment's databases stayed
  unmigrated after `eb create` (`GET /events` 500'd). Fixed with a
  `.platform/hooks/postdeploy` script that execs into each service's
  container and runs the same `alembic upgrade head` + seed steps the
  Makefile's `migrate`/`seed` targets run locally.

Live evidence: logged in as `alice` through the deployed frontend, browsed
seeded events, held a seat (`POST /bookings` succeeded) — full round trip,
not just page load. Security group opened to exactly port 80 + Keycloak's
8081 as part of this task (pulled forward from T2, since T1's own
done-when criterion needed it — see T2 below).

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
> at ~$20 (§13) as a safety net. Confirm
> with the user before applying security-group changes or creating billing
> alerts, same as T1. If the session is ending with the instance still
> running, stop it before finishing.

**Done when:** the deployed environment runs on EB-managed environment
properties with no secret committed to the repo, the security group opens
exactly the two ports that need to be public, and the Budgets alert is
live. Report evidence: Deployment figures.

**Done (2026-08-24), mostly as a T1 carryover.** All three verified:
- **Secrets**: all 9 (7 DB credential values, Keycloak admin password +
  client secret, 2 Stripe test-mode keys — the ~10 estimate rounded up)
  confirmed present via `eb printenv`, sourced from local `.env` into EB
  environment properties at `eb create` time, never committed — verified by
  `git log -p` across every commit this phase, no real secret value found
  (only the pre-existing, already-committed `changeme` seed-user/demo-client
  placeholders, not a leak).
- **Security group**: `aws ec2 describe-security-groups` confirms exactly
  two inbound rules — TCP 80 and TCP 8081 (Keycloak), both `0.0.0.0/0`,
  nothing else. Opened during T1, not a separate step here.
- **Budgets alert — deviation from plan.** Per an explicit user decision
  mid-phase, reusing the existing pre-provisioned `Budget-of-Cost` budget
  ($10/month, alerts at $6 forecasted / $8 actual to
  `ansil.mishra316@gmail.com`) instead of creating a new ~$20 one as
  originally planned above. Tighter than the plan's $20, not looser — the
  existing alert already covers this phase's spend at a lower threshold, so
  a second budget would have been redundant.

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

**Done (2026-08-24).** Full round trip live-verified against
`http://event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`,
logged in as `bob`: browsed seeded events, held seat 1-2 on "Wandering
Notes: Reunion Tour" (`POST /bookings`), paid via a real Stripe test-mode
charge, confirmation page showed `status: pending` (expected — confirmation
is webhook-driven, not synchronous, per §17), then verified via
`GET /bookings/events/{id}/tickets` that the webhook actually landed and
the ticket flipped `held` → `booked`. Webhook delivery used
`stripe listen --forward-to http://<eb-cname>/payments/webhook` (its
printed signing secret matched the `STRIPE_WEBHOOK_SECRET` already set on
EB from T1/T2, so no `eb setenv` was needed) — all four forwarded events
(`payment_intent.succeeded`, `payment_intent.created`, `charge.succeeded`,
`charge.updated`) returned `200` from the deployed `payment-service`.
Five screenshots captured at
`docs/report/assets/deployment-flow/01-browse-events.png` through
`05-booking-confirmed.png` (browse, Keycloak login, seat map, checkout,
confirmation). Nothing broke that T1 hadn't already caught — no new fix
needed here.

---

## P10.T4 — Stop/terminate discipline + CNAME stability writeup

**Prompt to Claude Code:**
> Stop (not terminate) the EB environment's instance once T3's validation is
> done, per §13's decided stop-vs-terminate reasoning (small dollar
> difference, terminate risks image re-pull and platform drift, stopping
> keeps the environment CNAME stable). Document in the Deployment Flow
> report chapter: the actual stop procedure used, confirmation the CNAME is
> still the same after a stop/restart cycle (verify this live, don't just
> cite the decision), and the real cost incurred so far against the
> ~$7–12 all-in baseline in "Credential & execution model" above (itself a
> reconciliation of §13's original ~$3-6 compute estimate). Note explicitly
> that full termination is deferred until the project is submitted and
> graded (§13), not done now.

**Done when:** the instance is stopped, CNAME stability is live-verified
(not just asserted from the decision), and the cost writeup reflects real
AWS billing console numbers, not an estimate. Report evidence: Cost
writeup.

**Done (2026-08-24), with a real correction to §13 found along the way.**

A plain `aws ec2 stop-instances` on this environment did **not** just pause
it — the environment's Auto Scaling Group (present even in single-instance
tier) treated the stop as a health-check failure and replaced the instance
outright (terminated the old one, launched a fresh one with a new EBS
volume, wiping all container-local data). Live-verified via
`aws autoscaling describe-scaling-activities` and by watching the seed
data's event UUIDs change after the "stop." Full finding and the corrected
procedure — suspend `HealthCheck`/`ReplaceUnhealthy`/`AZRebalance` on the
ASG before stopping — recorded as a §13 amendment in `decisions-log.md`,
since this corrects a locked decision's stated reasoning, not just a
build-log note. The corrected procedure was then live-verified for real:
a second stop/start cycle with processes suspended kept the same instance
ID and the same (post-replacement) data intact — a true pause/resume.

**CNAME stability**: confirmed identical
(`event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`)
across both the accidental replacement and the corrected stop/start —
holds regardless of which instance is behind it, exactly as §12/§13
claimed (EB re-associates the environment's Elastic IP automatically).

**Leftover-resource sweep** (prompted mid-task, worth recording since nothing
in the original plan called for it): checked for anything the accidental
replacement might have stranded — no orphaned EBS volume (old one
auto-deleted on termination), no orphaned Elastic IP (correctly
re-associated with the replacement instance), no stray snapshots, no
unattached network interfaces. Clean.

**Cost writeup.** AWS Cost Explorer has a documented ~24-48h billing-data
lag, so today's usage doesn't appear there yet — confirmed by querying it
directly (`aws ce get-cost-and-usage` for today returns no line items).
The figure below is computed from actual measured EC2 runtime
(`aws autoscaling describe-scaling-activities` + `describe-instances`
timestamps, not an estimate) against the confirmed real on-demand rate
(AWS Pricing API, $0.1792/hr t3.xlarge in `ap-south-1`) — real inputs, just
not sourced from the console itself, which isn't populated yet. Should be
spot-checked against the console in a day or two once it catches up.

- Instance 1 (`i-0259e0afad0822908`, the original `eb create` launch
  through all of T1–T3's testing): 15:08:27–15:44:21 UTC ≈ 35m54s
- Instance 2 (`i-042877e8a95e2a68f`, the accidental replacement, across
  both the uninformed stop/start and the corrected one):
  15:44:23–~15:52:11 UTC (≈7m48s) + 15:53:10–15:57:34 UTC (≈4m24s) ≈ 12m12s
- **Total EC2 runtime this phase: ≈48 minutes (≈0.80 hrs)**
- Compute: 0.80 hr × $0.1792/hr ≈ **$0.14**
- EBS (8GB gp3, ≈50 min wall-clock existence so far): ≈$0.001
- S3 (deployment bundles/logs, ~1.7MB): negligible
- **Total this phase so far: ≈$0.15** — far under the ~$7–12 all-in
  baseline (expected: that baseline covers the whole project's lifecycle,
  not one session), and nowhere near the `Budget-of-Cost` alert's $6/$8
  thresholds.

Full termination remains deferred until the project is submitted and
graded (§13) — not done now. The instance is stopped (ASG replacement
processes left suspended, harmless for a single-instance environment)
as this session ends.

---

## Phase 10 exit checklist (all must pass before P11 resumes)

- [x] P10.T1 — hardcoded-localhost gap fixed and verified locally; EB
      app/environment created (Docker platform branch, AL2023,
      single-instance, t3.xlarge); stack running with a working login+API
      round trip against the real EB address. Evidence: live login as
      `alice` + seat hold against
      `event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`,
      see T1's "Done" note above.
- [x] P10.T2 — secrets moved to EB environment properties (none committed);
      security group opens exactly ports 80 and the Keycloak port; existing
      `Budget-of-Cost` alert ($10/month) reused in place of a new ~$20 one
      (user decision, recorded in T2's "Done" note above). Evidence:
      `eb printenv` + `git log -p` secret scan + `describe-security-groups`,
      see T2's "Done" note above.
- [x] P10.T3 — browse→book→pay→confirm live-verified against the deployed
      EB environment; screenshots captured. Evidence: ticket status
      `held`→`booked` confirmed via API after a real Stripe test-mode
      charge + webhook, see T3's "Done" note above; 5 screenshots at
      `docs/report/assets/deployment-flow/`.
- [x] P10.T4 — instance stopped; CNAME stability live-verified across the
      stop/restart; real cost writeup against the baseline. Evidence: T4's
      "Done" note above — includes a real §13 correction (ASG replaces a
      directly-stopped instance unless its processes are suspended first),
      live-verified fix, and a leftover-resource sweep, all beyond the
      original task scope.
- [x] Credentials used throughout this phase were an IAM user (with
      `AdministratorAccess`) — not root account keys; confirmed via
      `aws sts get-caller-identity` (`arn:aws:iam::427597698460:user/anshilM`)
      and `list-groups-for-user`/`list-attached-group-policies` (policy
      attached via the `admin` group). No access key was ever pasted into
      chat or committed — `aws login` was used, which never writes a static
      key to disk at all (temporary, auto-refreshing credentials).
- [x] Instance-stop discipline was applied at the end of every session that
      touched the live environment, not just after T3 — the instance was
      stopped (with ASG processes suspended, per the §13 correction) as
      this session ends; no session left it running unintentionally.
- [x] Full suite green (unit + integration) across all 5 services — 262
      passed, 0 failed (event-service 80, search-service 25, booking-service
      87, payment-service 24, notification-service 15, shared-auth 31);
      frontend's 13 tests (including 4 new SHA-256 vectors) also green.
      Nothing in this phase's changes broke local `docker compose up`.
- [x] Live walkthrough done at CHECKPOINT — this phase's walkthrough is
      inherently live (it's the AWS validation run itself, P10.T3) rather
      than a separate step. See T3's "Done" note above.
- [x] `docs/architecture.html` updated to current state — new §09 (AWS
      Deployment) section, header badges, and the "provisioned, not built
      yet" list (now empty — both long-tracked gaps closed) all reflect the
      real EB environment, not just local compose.
- [x] `decisions-log.md` delta check — both questions resolved yes. §12
      amendment added: corrects "no auto-scaling group" (every EB
      environment has one, even single-instance tier — the root cause of
      T4's finding), plus the durable summary of the three real
      implementation gaps and the credential model. §13 amendment added
      separately for the stop-vs-terminate correction itself.
- [x] `CLAUDE.md` self-update check — explicitly checked, not assumed. Two
      updates made: Repo layout's `/infra` line now mentions
      `build-eb-bundle.sh`/`.platform/hooks/`; Tech stack now names AWS
      Elastic Beanstalk. No new coding convention needed — the ASG/stop
      finding is an operational fact, fully recorded in decisions-log §13,
      not a code pattern.
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
