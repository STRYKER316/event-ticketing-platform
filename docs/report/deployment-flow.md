# Deployment Flow

*Status: Deployed — full topology, configuration, and validation are all
live-verified against a real AWS environment, not described in the
abstract. Cost figures are Measured (real billing inputs), not estimated.*

## Overview

The system is deployed to AWS Elastic Beanstalk (EB), Docker platform
branch (AL2023), single-instance mode — no load balancer, no manual
auto-scaling (§12). EB deploys the project's existing `docker-compose.yml`
directly, so the same container topology that runs under `docker compose
up` locally also runs on the deployed instance — local-first (§25) means
this is a late, thin deployment checkpoint, not where development
happens.

Live environment: `event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`,
region `ap-south-1` (Mumbai, a deliberate user choice over §13's original
`us-east-1` cost baseline), instance type `t3.xlarge` per §12's worked-out
resource budget for a 14-15 container stack (Elasticsearch and Kafka are
both memory-hungry; three consolidations — one Postgres process for three
logical databases, Kafka in KRaft mode with no Zookeeper, Keycloak in dev
mode with its embedded DB — keep the always-on footprint workable on one
instance).

Credentials used throughout: an IAM user (`anshilM`, not root) with
`AdministratorAccess` attached via an `admin` IAM group, authenticated via
`aws login` (temporary, auto-refreshing console-session credentials — no
static access key ever written to disk, safer than the originally-planned
`aws configure` flow). Confirmed via `aws sts get-caller-identity` before
treating any deployment step as real.

## Pre-deployment fixes: making a localhost-built app work on a public CNAME

Nothing in the stack had ever been reached by anything other than
`localhost` before this phase. Three real gaps surfaced, none anticipated
until actually hit — full narrative in `docs/phases/phase-10-kickoff.md`'s
T1 section and decisions-log §12's amendment; summarized here as the
durable deployment-facing fact:

- **Build-time `localhost` URLs.** The frontend's Vite build had baked
  `VITE_EVENT_SERVICE_URL`/`VITE_SEARCH_SERVICE_URL`/`VITE_BOOKING_SERVICE_URL`
  and the Keycloak OIDC issuer in as literal `http://localhost...`
  constants, and Keycloak's realm export locked `redirectUris`/
  `webOrigins` to `localhost` origins the same way. Fixed three ways: the
  three service URLs are now relative (Traefik already proxies both the
  frontend and the backend APIs from the same origin, so same-origin
  requests need no absolute host), the Keycloak issuer is resolved at
  runtime from `window.location` instead (Keycloak is reached directly on
  its own port, not proxied through Traefik, so it genuinely can't be
  relative), and the realm config is rendered from a template by a
  Keycloak entrypoint script driven by an `APP_ORIGIN` environment value —
  the same container image now works unmodified against either `localhost`
  or the real EB CNAME.
- **`crypto.subtle` requires a secure context.** PKCE's `code_challenge`
  computation needs `crypto.subtle`, which browsers restrict to HTTPS or
  specifically the `localhost` hostname — every local test passed, then
  the login button silently did nothing against the deployed CNAME's
  plain-HTTP hostname. Fixed with a hand-written, pure-JS SHA-256 fallback
  (`frontend/src/auth/subtleCryptoPolyfill.ts`), verified against NIST
  test vectors, installed only when the native implementation is missing —
  keeps PKCE at full S256 strength rather than silently downgrading to
  `plain`.
- **EB's Docker-Compose deploy needs a flat bundle.** `infra/docker-compose.yml`'s
  build contexts (`../services`, `../frontend`) reach one level above
  `infra/`, which doesn't resolve under EB's Docker-Compose deploy — it
  expects the compose file at the bundle root with every build context
  nested underneath. Fixed with `infra/build-eb-bundle.sh`, which
  assembles a flat, self-contained deployment bundle without touching the
  canonical compose file used for local dev. EB also has no host-side
  Python/`uv`, so a `.platform/hooks/postdeploy` script execs into each
  service's own container to run the same `alembic upgrade head` + seed
  steps the Makefile's `migrate`/`seed` targets run locally, so a freshly
  created environment's databases don't stay unmigrated.

Each fix was verified against the real deployed environment, not assumed
from the mechanism alone: a full login-through-API round trip run first
against `docker compose up`, then again against the live EB CNAME.

## Secrets and environment properties

The ~10 real secret values identified in the pre-read audit — 7 database
credential pairs, the Keycloak admin password and client secret, and the
2 Stripe test-mode keys — moved from the local, gitignored `.env` into EB
environment properties at `eb create` time, per §19's decision that EB
environment properties are where AWS-side secrets/config live (no
separate secrets manager). Confirmed via `eb printenv` and, separately, a
`git log -p` scan across every commit in this phase specifically for a
leaked secret — none found; the only credential-shaped strings present in
git history are the pre-existing, already-committed `changeme` seed-user
and demo-client placeholders used for local dev, not a real leak.

## Network exposure

Keycloak is reached directly on its own published port rather than being
proxied through Traefik (`infra/docker-compose.yml`'s `keycloak` service
carries no `traefik.*` labels), so the EB environment's security group
needs two public inbound rules, not one — confirmed via
`aws ec2 describe-security-groups`:

- **TCP 80** — Traefik, fronting the frontend and every backend API
- **TCP 8081** — Keycloak, needed directly for the OIDC login redirect

Both rules are the only two present, both `0.0.0.0/0` — nothing broader
than what the login flow actually requires is open.

## Cost safety net

An AWS Budgets alert is live against this environment's spend. Rather
than provisioning a new ~$20 budget as originally planned (§13), the user
opted to reuse an existing pre-provisioned `Budget-of-Cost` budget
($10/month, alerting at $6 forecasted / $8 actual to the account owner's
email) — tighter than the original plan, not looser, so no coverage gap.

## Validation run: browse → book → pay → confirm, against the real deployment

The full customer flow was exercised live against the deployed EB
environment, not local Docker, logged in as a real seeded user (`bob`)
through a real browser session:

1. Browse the seeded event catalog served from the deployed frontend.
2. Log in via the deployed Keycloak instance (Authorization Code + PKCE).
3. Open an event's interactive seat map and hold seat 1-2 on "Wandering
   Notes: Reunion Tour" (`POST /bookings` against the deployed
   `booking-service`).
4. Pay via a real Stripe test-mode charge, initiated through Booking
   Service's synchronous `POST /bookings/{id}/pay` call into Payment
   Service (§9 amendment) — the deployment's one exercised instance of
   this system's only synchronous inter-service call.
5. The confirmation page showed `status: pending` immediately after
   paying — expected, not a bug: confirmation is webhook-driven, not
   synchronous, per §17. Stripe webhook delivery was forwarded to the
   deployed environment via `stripe listen --forward-to
   http://<eb-cname>/payments/webhook`; all four forwarded events
   (`payment_intent.succeeded`, `payment_intent.created`,
   `charge.succeeded`, `charge.updated`) returned `200` from the deployed
   `payment-service`. `GET /bookings/events/{id}/tickets` afterward
   confirmed the ticket had actually flipped `held` → `booked` once the
   webhook landed — the full asynchronous confirmation path, not just a
   page render.

Five screenshots were captured across this run, at
`docs/report/assets/deployment-flow/`:

| # | File | Step |
|---|---|---|
| 1 | `01-browse-events.png` | Event catalog, served from the deployed frontend |
| 2 | `02-keycloak-login.png` | Login against the deployed Keycloak instance |
| 3 | `03-seat-map.png` | Interactive seat map, live seat status |
| 4 | `04-checkout.png` | Checkout / Stripe test-mode payment |
| 5 | `05-booking-confirmed.png` | Confirmation page (`pending`, ahead of the webhook landing) |

Nothing surfaced during this run that the earlier local verification
hadn't already caught — no new fix was needed at this stage.

## Stop/terminate discipline: a real correction to a locked decision

§13 originally reasoned that stopping (not terminating) the EC2 instance
between sessions was a cheap, safe way to avoid re-provisioning overhead
while keeping the environment's CNAME stable. Executing that plan found
it wrong, worth documenting precisely since it corrects a
previously-locked decision's stated reasoning, not just a build-log
footnote: a plain `aws ec2 stop-instances` against this environment did
**not** pause it. Every Elastic Beanstalk environment —
single-instance tier included — is backed by an Auto Scaling Group
(min=max=desired=1) with `HealthCheck`/`ReplaceUnhealthy` processes active
by default; those processes treated the stopped instance as a health-check
failure and **replaced it outright** — terminated the stopped instance,
launched a fresh one with a new EBS root volume, wiping every
container-local data volume (Postgres, MongoDB, Elasticsearch, Redis,
Kafka). Caught live: the booking made during the validation run vanished,
and the reseeded baseline events came back under entirely new UUIDs.
Confirmed via `aws autoscaling describe-scaling-activities`: "an instance
was taken out of service in response to an EC2 health check indicating it
has been terminated or stopped."

**Corrected procedure**, live-verified to actually work: suspend the ASG's
`HealthCheck`, `ReplaceUnhealthy`, and `AZRebalance` processes before every
`aws ec2 stop-instances`, and resume them after the matching
`start-instances` (leaving them suspended between sessions is harmless for
a single-instance environment with no real auto-scaling to begin with). A
second stop/start cycle run with processes suspended kept the same
instance ID and the same (post-replacement) data intact — a true
pause/resume, matching what §13 originally intended. Recorded as
amendments to both §12 (the "no auto-scaling group" claim was simply
wrong — single-instance means no load balancer and no *scaling*, not no
ASG) and §13 (the corrected stop procedure itself) in `decisions-log.md`.

**CNAME stability** held throughout, exactly as §12/§13 claimed: the
environment's CNAME
(`event-ticketing-env.eba-uvwm2tcf.ap-south-1.elasticbeanstalk.com`)
stayed identical across both the accidental replacement and the corrected
stop/start — EB re-associates the environment's Elastic IP automatically
regardless of which underlying instance is behind it.

A leftover-resource sweep after the accidental replacement found nothing
stranded: no orphaned EBS volume (the old one auto-deleted on
termination), no orphaned Elastic IP, no stray snapshots, no unattached
network interfaces.

## Cost writeup (Measured)

AWS Cost Explorer carries a documented ~24-48 hour billing-data lag and
returned no line items for this phase's usage when queried directly, so
the figures below are computed from measured EC2/EBS timestamps
(`aws autoscaling describe-scaling-activities` + `describe-instances`)
against the confirmed real on-demand rate for `ap-south-1`
($0.1792/hr t3.xlarge, via the AWS Pricing API — `ap-south-1` runs higher
than the $0.1664/hr `us-east-1` figure §13's original estimate was priced
against) — real measured inputs, not an estimate, just not yet
cross-checked against the console itself.

| Instance | Window | Duration |
|---|---|---|
| `i-0259e0afad0822908` (original `eb create`, all of T1-T3) | 15:08:27-15:44:21 UTC | ≈35m54s |
| `i-042877e8a95e2a68f` (accidental replacement + corrected stop/start) | 15:44:23-~15:52:11 UTC and 15:53:10-15:57:34 UTC | ≈12m12s |
| **Total EC2 runtime, Phase 10** | | **≈48 minutes (≈0.80 hrs)** |
| `i-042877e8a95e2a68f` (P11.T5 demo-script recording session, 2026-08-25) | 05:51:21-06:03:02 IST (00:21:21-00:33:02 UTC) | ≈11m41s |
| **Total EC2 runtime, project to date** | | **≈60 minutes (≈0.99 hrs)** |

- Phase 10 compute: 0.80 hr × $0.1792/hr ≈ **$0.14**
- P11.T5 demo session compute: 0.195 hr × $0.1792/hr ≈ **$0.035**
- EBS (8GB gp3, cumulative wall-clock existence to date): ≈$0.002
- S3 (deployment bundles/logs, ~1.7MB): negligible
- **Total project-to-date: ≈$0.18**

Far under the ~$7-12 all-in project-lifecycle baseline (§13, already
reconciled from its original ~$3-6 `us-east-1` compute-only estimate) and
nowhere near the `Budget-of-Cost` alert's $6/$8 thresholds. That baseline
covers the whole project's EC2 lifetime, not one session, so a cumulative
≈$0.18 across two deployment/validation sessions sitting well under it is
the expected outcome, not evidence the baseline was loose.

The P11.T5 demo-script recording session used the same corrected
pause/resume procedure documented above — `aws ec2 start-instances`
against the already-stopped, ASG-suspended instance (same instance ID
throughout, confirmed via `describe-auto-scaling-groups` before and
after), followed by `aws ec2 stop-instances` once the recording was done.
The instance ID staying identical across the whole session is itself a
second live confirmation of the corrected procedure, beyond the one
already recorded in Phase 10.

The instance is stopped (ASG replacement processes left suspended,
harmless for a single-instance environment) as of the end of both
sessions. Full termination is deliberately deferred until the project is
submitted and graded (§13) — the deployed environment may still be needed
again before then.

## What this section still needs

None remaining. The demo recording this section previously flagged as
outstanding (P11.T5) is now complete — see `docs/report/demo-script.md`
(linked from the assembled report's Appendix B) for the full reproducible
walkthrough and its live-run record.
