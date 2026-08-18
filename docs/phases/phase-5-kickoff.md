# Phase 5 Kickoff — Notification Service + Retry/DLQ (Kafka #3)

**Goal of this phase:** the hand-rolled aiokafka retry/backoff/DLQ story
(§17) — a Notification Service that consumes the `notifications` topic
(booking-confirmed, payment-confirmed, refund-failed), "delivers" via
structured log output (§19 — no real email provider), and escalates a
delivery failure through an increasing-backoff retry ladder to a dead-letter
topic rather than silently dropping it. This is the citable engineering
story `CLAUDE.md`'s "acknowledged FastAPI cost, done deliberately" refers
to — Spring Kafka has `@RetryableTopic` built in; aiokafka doesn't.

**How to use this file:** run the four tasks below **in order**, one per
Claude Code session. Commit after each (small, green commits). `main` stays
bootable at every step. Give Claude Code the repo plus `decisions-log.md`
and `master-development-plan.md` as context so it stays anchored to the
locked decisions (referenced by § below).

**Entry deps:** Phase 3 (Booking Service, `PaymentOutcomeConsumer`) and
Phase 4/6 (Payment Service, `handle_webhook_event`, `refund_payment`,
`NotificationProducer`) all complete and checkpointed. Per the locked
report-first build order (§27: P8 → P4 → P6 → **P5** → P7 → P10 → P9/P11),
this is the next phase. The `notifications` topic and its producer-side
`NotificationMessage`/`NotificationAction` schema already exist on Payment
Service (built in P6.T3, `REFUND_FAILED` only) — this phase adds the
consumer that was deliberately deferred then, plus the two other trigger
call sites P6.T3 explicitly left alone (booking-confirmed, payment-confirmed).

**Three design gaps found and resolved before this task list was
finalized** (all recorded as a decisions-log §17 amendment, same practice
as the §7.2/§9/§16/§22 amendments — surfaced as open questions rather than
silently guessed at):

1. **No DB, confirmed rather than left open** (`CLAUDE.md`/§4/§19 left it
   either/or). **Resolved: no DB.** Nothing in this phase reads delivery
   history back; Kafka itself carries all the retry state needed (#2).
2. **Retry state travels on the message, not in a table.** A
   `RetryEnvelope { attempt, original, last_error }` wraps a message once
   it enters the retry path. **Three topics:** `notifications` (one
   attempt, no backoff), `notification-retry` (re-attempts with increasing
   backoff, self-republishes with `attempt+1` on failure),
   `notification-dlq` (terminal once `attempt > retry_max_attempts`). A
   `DlqConsumer` logs on arrival for demo visibility — nothing reprocesses
   out of it automatically.
3. **Nothing in this system can make a real delivery attempt fail** — no
   external dependency exists to fail against (log/console only, §19).
   **Resolved the same way P8.T5 resolved an analogous gap**: a
   `Settings.simulated_failure_attempts` toggle, off by default, that
   deterministically fails delivery while `attempt <= that value` —
   documented plainly as a demo/test instrument, not a naturally occurring
   failure.

Full reasoning for all three is in `decisions-log.md`'s new §17 amendment;
this file only carries what Claude Code needs to build it.

**Process notes specific to this phase (per `CLAUDE.md`):**
- **Build-then-test for everything in this phase** — nothing here is on
  `CLAUDE.md`'s test-first list (dual hold strategies P3, payment/webhook
  idempotency P4 specifically). The retry ladder is new mechanism, but it's
  plumbing/sequencing correctness (does message X reach topic Y after N
  failures), not the kind of concurrent-correctness-under-race property
  test-first is reserved for here.
- No dedicated adversarial `/code-review` pass is required for P5 (only P3
  and P8 get one) — self-verification plus the routine `/pre-pr` gate at
  CHECKPOINT is sufficient.
- **Manual offset commit, even with no DB write** — the same
  `enable_auto_commit=False` + commit-only-after-the-record-is-fully-
  handled discipline `CLAUDE.md`'s Conventions section states for "a Kafka
  consumer with its own DB write" applies here for the same underlying
  reason, just with a Kafka republish standing in for a DB write as "the
  thing that must finish before the offset advances": a crash between
  receiving a `notifications` message and either logging its successful
  delivery or republishing it to `notification-retry` must not silently
  lose that message to an auto-committed offset.
- **New service, deliberately thinner than the template.** Copy
  `event-service`'s scaffolding shape (`main.py` app factory, `core.py`,
  Manager pattern) per `CLAUDE.md`'s "new service = copy the template"
  rule, but this service has no SQLAlchemy/Mongo/Redis and — because it
  exposes no protected routes (no organizer/user actions, `/healthz` is
  its only endpoint and is public) — no `shared_auth` dependency either.
  Note this explicitly in P5.T1's commit rather than leaving an unused
  import in place "for consistency."
- `notification-service` is not yet in the root `uv` workspace
  (`services/pyproject.toml`'s `[tool.uv.workspace] members`) or
  `infra/docker-compose.yml` — both are P5.T1 work, not already done.
  `NOTIFICATION_SERVICE_PORT=8005` is already reserved in `.env.example`.

---

## P5.T1 — Scaffold (no DB, per this phase's resolved §17 amendment)

**Prompt to Claude Code:**
> Scaffold `notification-service` following `event-service`'s template
> shape (`CLAUDE.md`'s per-service layering), thinned per this phase's
> "deliberately thinner than the template" process note above — no `db/`
> folder, no SQLAlchemy engine/session factory in `core.py`, no
> `shared_auth` import anywhere (this service exposes no protected routes).
>
> ```
> services/notification-service/app/
>   main.py                    # app factory, lifespan (Kafka consumers only)
>   api/
>     health.py                  # GET /healthz — public, no DB to check,
>                                 # returns {"status": "ok"} unconditionally
>   logic/
>     notification_manager.py     # NotificationManager.deliver(message, attempt)
>     helpers/
>       backoff.py                 # compute_backoff_seconds(attempt) -> float
>   kafka/
>     schemas.py                    # NotificationAction, NotificationMessage,
>                                    # RetryEnvelope — this service's own
>                                    # independently-defined copy, same pattern
>                                    # every other service's Kafka schemas.py
>                                    # already follows (mirrored, not shared)
>     producers.py                   # publishes to notification-retry / notification-dlq
>     consumers.py                    # NotificationConsumer, RetryConsumer, DlqConsumer
>   core.py                            # Settings, structured logging, Kafka
>                                       # producer singleton (mirror payment-
>                                       # service/app/core.py's producer
>                                       # section exactly; drop everything
>                                       # DB-related)
> tests/
>   unit/
>   integration/
> ```
>
> `Settings` fields: `notification_service_port: int = 8005`,
> `kafka_bootstrap_servers`, `notifications_topic: str = "notifications"`,
> `notification_retry_topic: str = "notification-retry"`,
> `notification_dlq_topic: str = "notification-dlq"`,
> `notification_consumer_group_id`, `notification_retry_consumer_group_id`,
> `notification_dlq_consumer_group_id` (one group ID per topic, matching
> every other service's per-topic consumer-group convention),
> `retry_max_attempts: int = 3`, `retry_base_backoff_seconds: float = 2.0`,
> `retry_backoff_cap_seconds: float = 30.0`,
> `simulated_failure_attempts: int = 0` (§17 amendment #3 — 0 means
> disabled, normal operation never simulates a failure; document the field
> as a demo/test instrument in its own comment, not a production knob).
>
> Add the `Dockerfile` mirroring `payment-service/Dockerfile`'s shape (no
> `_shared/auth` copy step — this service doesn't depend on it), add
> `notification-service` to `services/pyproject.toml`'s
> `[tool.uv.workspace] members`, and add its `infra/docker-compose.yml`
> block mirroring `payment-service`'s (no Postgres `depends_on`, no
> `POSTGRES_*`/`*_DB_*` env vars — only `KAFKA_BOOTSTRAP_SERVERS` and the
> three topic/group-id vars above), `depends_on: kafka: condition:
> service_healthy` only, Traefik label
> `traefik.http.routers.notification-service.rule: PathPrefix(\`/notifications\`)`
> (even though nothing calls it externally yet — `/healthz` still needs to
> be reachable through the gateway the same way every other service's is,
> per `CLAUDE.md`'s per-service Traefik convention). `main.py`'s `lifespan`
> starts and closes the Kafka producer singleton only — no consumers wired
> yet, that's P5.T2/T3.

**Done when:** `docker compose up notification-service` boots healthy;
`/healthz` returns `200 {"status": "ok"}` with no auth required, verified
directly against the container (`docker compose exec notification-service
... /healthz`) — **not** through Traefik at `/notifications/healthz`, which
404s. Checked live during this task: none of this system's non-root-prefix
services (`search-service` at `/search`, presumably `booking-service` at
`/bookings` too) actually expose `/healthz` through their own Traefik
`PathPrefix` either, since no service strips its prefix before the request
reaches its own bare `/healthz` route — a pre-existing repo-wide pattern,
not something this task introduces or needs to fix. Report evidence: none
(scaffold only, matches every other phase's T1 row in
`master-development-plan.md`).

---

## P5.T2 — Kafka #3 consumer: booking-confirmed / payment-confirmed / refund-failed → log

**Prompt to Claude Code:**
> **Two missing producer call sites first** (§22 amendment #3 deliberately
> deferred both to this phase):
>
> *Booking Service* — add `NotificationAction`/`NotificationMessage` to
> `booking-service/app/kafka/schemas.py`, mirroring payment-service's
> shape but scoped to what this service ever sends:
> `NotificationAction.BOOKING_CONFIRMED = "booking_confirmed"` only (don't
> pre-add unused members, same restraint P6.T3 exercised the other way).
> `NotificationMessage` fields: `action`, `booking_id`, `reason: str | None
> = None` (this trigger has no natural reason text — leave it `None`, don't
> invent a placeholder string). Add a `NotificationProducer` to
> `booking-service/app/kafka/producers.py` alongside the existing
> `BookingCancelledProducer`, same `send_and_wait` shape, keyed by booking
> ID, publishing to a new `notifications_topic` setting (env var
> `NOTIFICATIONS_TOPIC=notifications` — same topic name Payment Service
> already publishes to, both services are producers on it). Wire it into
> `PaymentOutcomeConsumer._transition_with_retry`
> (`booking-service/app/kafka/consumers.py`): after a successful transition
> where `message.action is PaymentOutcomeAction.SUCCEEDED` (i.e. right
> alongside the existing `confirm_hold` call), publish
> `NotificationAction.BOOKING_CONFIRMED` for `message.booking_id`. Do
> **not** publish on the `FAILED` branch — a failed payment has nothing to
> confirm, and isn't one of the three triggers §7 point 3 names.
>
> *Payment Service* — add `NotificationAction.PAYMENT_CONFIRMED =
> "payment_confirmed"` to the existing enum in
> `payment-service/app/kafka/schemas.py`, and make `NotificationMessage
> .reason` optional (`str | None = None`) — `REFUND_FAILED` still supplies
> real reason text, `PAYMENT_CONFIRMED` won't. Wire
> `PaymentManager.handle_webhook_event` (`app/logic/payment_manager.py`):
> after a successful transition to the `succeeded` outcome (same place
> `producer.publish_outcome(payment)` already runs, before `commit()` —
> same publish-before-commit reasoning that call site already documents),
> also publish `NotificationAction.PAYMENT_CONFIRMED` via the existing
> `NotificationProducer` for `payment.booking_id`. `handle_webhook_event`'s
> signature needs the `NotificationProducer` added alongside its existing
> `PaymentOutcomeProducer` parameter; update its route's `Depends(...)` and
> the one other caller (`refund_payment` already takes it, per P6.T2/T3 —
> reuse the same provider function, don't add a second one).
>
> **Notification Service itself.** `kafka/schemas.py`: this service's own
> independently-defined `NotificationAction` (all three members —
> `BOOKING_CONFIRMED`, `PAYMENT_CONFIRMED`, `REFUND_FAILED` — since this is
> the one place that has to recognize every producer's action) and
> `NotificationMessage` (`action`, `booking_id`, `reason: str | None =
> None`), mirroring the producer sides. `RetryEnvelope { attempt: int,
> original: NotificationMessage, last_error: str }` — not used by this
> task's consumer yet (P5.T3), define it now since `kafka/schemas.py` is
> one file.
>
> `logic/notification_manager.py`: `NotificationManager.deliver(message:
> NotificationMessage, attempt: int) -> None`. If
> `get_settings().simulated_failure_attempts >= attempt` (§17 amendment #3
> — 0 by default, so this branch is dead in normal operation), raise a
> dedicated `SimulatedDeliveryFailure` exception with a message naming it
> as a deliberate test instrument, not a real error. Otherwise, log
> `notification_delivered` at `info` (`action`, `booking_id`, `attempt`) —
> this **is** the delivery per §19 (log/console only, no real email
> provider).
>
> `kafka/consumers.py`: `build_notification_consumer()` (subscribes to
> `notifications_topic`, `enable_auto_commit=False`, matching every other
> service's `build_*_consumer()` shape) and `NotificationConsumer`. `_handle`
> parses `NotificationMessage` (log-and-return on parse failure, same
> unretriable-poison-message handling `ProvisioningConsumer`/
> `PaymentOutcomeConsumer` already use — a malformed payload won't become
> parseable on retry, so it doesn't enter the retry ladder at all), then
> calls `NotificationManager().deliver(message, attempt=1)` — the initial
> attempt is always `1` (§17 amendment #2). On success: commit offset (via
> `_consume_with_manual_commit`'s existing after-handle commit — reuse it if
> a shared consumer-loop helper is worth factoring out across services, or
> keep it local; use judgement, note the choice in this task's build-log
> entry, same latitude P6.T2 was given for its own consumer helper). **On
> failure this task only logs the failure and returns** (`notification_delivery_failed`,
> at `warning` — an expected, handled failure per the log-level-discipline
> convention) — the retry-ladder republish is P5.T3, not this task; don't
> build it early just because the exception is already being caught here.
>
> Wire `NotificationConsumer` into `main.py`'s `lifespan`, same
> `asyncio.create_task` + `_log_if_died` pattern every other multi-consumer
> service (`booking-service`, `payment-service`) already uses.

**Done when:** publishing a real `booking.confirmed`-triggering payment
(the existing `pay` flow's success path) produces a real
`booking_confirmed` log line from Notification Service's consumer, and a
real webhook-driven success produces a real `payment_confirmed` log line —
both live-verified against the running stack, not just the throwaway
console-consumer check P6.T3 used as its own stopgap (this task is what
finally gives that topic a real consumer). A simulated refund failure
(P6.T3's existing test path) still produces a real `refund_failed` log
line, proving Notification Service now reads the same topic Payment
Service has been publishing to since Phase 6. Report evidence:
integration-point #3 diagram (this is what finally makes it a genuine
two-sided integration point, not producer-only).

---

## P5.T3 — Hand-rolled retry: backoff ladder → DLQ

**Prompt to Claude Code:**
> `logic/helpers/backoff.py`: `compute_backoff_seconds(attempt: int) ->
> float`, `min(settings.retry_base_backoff_seconds ** attempt,
> settings.retry_backoff_cap_seconds)` (§17 amendment #2's formula) — a
> pure function, unit-testable without a running consumer.
>
> `kafka/producers.py`: add `RetryPublisher` (mirrors this codebase's
> existing thin producer-wrapper shape) with `publish_retry(envelope:
> RetryEnvelope)` → `notification_retry_topic`, keyed by
> `envelope.original.booking_id`, and `publish_dlq(envelope: RetryEnvelope)`
> → `notification_dlq_topic`, same key.
>
> Update `NotificationConsumer._handle` (P5.T2): on a `deliver()` failure,
> instead of only logging, build a `RetryEnvelope(attempt=2,
> original=message, last_error=str(exc))` (attempt `2` — the initial
> attempt off `notifications` was `1`, per §17 amendment #2) and
> `publish_retry(envelope)` **before** committing the offset — same
> publish-before-commit-equivalent reasoning as this phase's manual-commit
> process note: a crash between the failed delivery and the retry-topic
> publish must redeliver from `notifications`, not silently drop the
> message.
>
> `kafka/consumers.py`: `build_retry_consumer()` +  `RetryConsumer`,
> subscribed to `notification_retry_topic`, `enable_auto_commit=False`.
> `_handle` parses `RetryEnvelope`, sleeps
> `compute_backoff_seconds(envelope.attempt)` (a real, in-process
> `asyncio.sleep` — this consumer has no other work competing for its
> attention while backing off, and aiokafka has no native delayed-delivery
> primitive to reach for instead, matching this phase's "hand-rolled, not
> Spring Kafka's `@RetryableTopic`" framing), then calls
> `NotificationManager().deliver(envelope.original, attempt=envelope.attempt)`.
> On success: log `notification_delivered_after_retry` (`attempt` in the
> log context, since this is meaningfully different from a first-attempt
> success), commit offset. On failure: if `envelope.attempt >
> settings.retry_max_attempts`, `publish_dlq(envelope)` (updating
> `last_error` to the latest exception) and log
> `notification_routed_to_dlq` at `error`; otherwise republish to
> `notification-retry` with `attempt=envelope.attempt + 1` and an updated
> `last_error`. Commit the offset only after whichever republish (or the
> DLQ publish) has finished — same reasoning as `NotificationConsumer`'s
> own publish-before-commit above.
>
> `build_dlq_consumer()` + `DlqConsumer`: subscribed to
> `notification_dlq_topic`, `enable_auto_commit=False`, `_handle` parses
> and logs `notification_landed_in_dlq` at `error` (`booking_id`, `action`,
> `attempt`, `last_error`) — visibility only, no reprocessing (§17
> amendment #2 — nothing in this system automatically recovers from the
> DLQ, matching what §17 actually promises: "not silently dropped," not
> "automatically retried forever").
>
> Wire both new consumers into `main.py`'s `lifespan` alongside
> `NotificationConsumer`.

**Done when:** with `SIMULATED_FAILURE_ATTEMPTS` set low enough to fail
once and then succeed (e.g. `1`), a real message published to
`notifications` is observed failing its first attempt, backing off, and
succeeding on retry — verified live against the running stack via
structured logs (`notification_delivery_failed` → `notification_delivered_after_retry`).
With `SIMULATED_FAILURE_ATTEMPTS` set at or above `retry_max_attempts + 1`,
the same message is observed exhausting every retry and landing in
`notification-dlq`, logged by `DlqConsumer`
(`notification_routed_to_dlq` then `notification_landed_in_dlq`) — not
silently dropped. Report evidence: retry/DLQ design writeup (§17 story) —
this is the phase's report centerpiece, the same way the dual-hold
strategies were P3's.

---

## P5.T4 — Tests

**Prompt to Claude Code:**
> Unit tests: `compute_backoff_seconds` (values at attempt 1/2/3 and the
> cap), `NotificationManager.deliver` (normal success logs and returns;
> `simulated_failure_attempts` configured to raise `SimulatedDeliveryFailure`
> at the right attempt boundary and not raise past it). A `testcontainers`
> Kafka integration suite (mirroring Phase 3/4/6's `community.kafka`
> pattern, `confluentinc/cp-kafka:7.6.0` + `.with_kraft()` per `CLAUDE.md`'s
> corrected Conventions note) covering: `NotificationConsumer` delivering a
> real `booking_confirmed`/`payment_confirmed`/`refund_failed` message
> without failure (no retry-topic publish); a forced failure (via
> `simulated_failure_attempts`) producing a `RetryEnvelope` on
> `notification-retry` with `attempt=2`; `RetryConsumer` processing that
> envelope, succeeding, and producing no further republish; a
> `simulated_failure_attempts` value that exhausts `retry_max_attempts`
> producing a message on `notification-dlq` with the correct final
> `attempt` and non-empty `last_error`; `DlqConsumer` consuming that
> message and logging it (assert via caplog/structlog capture, not by
> re-reading the topic a second time). Also add the two new booking-service/
> payment-service unit tests for their new producer call sites
> (`PaymentOutcomeConsumer` publishing `BOOKING_CONFIRMED` only on the
> `SUCCEEDED` branch; `handle_webhook_event` publishing `PAYMENT_CONFIRMED`
> alongside its existing `publish_outcome` call) — reuse each service's
> existing fake-producer test double pattern rather than inventing a new one.

**Done when:** full suite green across all three touched services
(`notification-service` new suite, `booking-service`/`payment-service`
incremented by their new producer-call-site tests). Report evidence:
testing-chapter material (§18).

---

## Phase 5 exit checklist (all must pass before P7)

- [x] End-to-end live walkthrough: a real `pay` success producing both
      `booking_confirmed` and `payment_confirmed` log lines; a real refund
      failure still producing `refund_failed` (regression check against
      P6.T3); the retry-then-recover and retry-then-DLQ demonstrations from
      P5.T3's done-when criteria, both run against the actual running
      stack with real log output captured. — Evidence: `build-log.md`'s
      P5.T2 entry (full book→pay→confirm flow, real `notification_delivered`
      log lines) and this file's Phase 5 CHECKPOINT entries (retry-then-
      recovery and DLQ-exhaustion both demonstrated twice — once during
      P5.T3, once again post-code-review-fixes against a fresh one-off
      container; `refund_failed` regression re-confirmed post-fix too).
- [x] Full test suite green (unit + testcontainers integration) across
      `notification-service`, `booking-service`, `payment-service` — final
      counts recorded in `build-log.md`. — Evidence: `notification-service`
      11/11 (5 unit, 6 integration), `booking-service` 72/72,
      `payment-service` 22/22, all re-run green after the CHECKPOINT fixes
      (build-log.md's final CHECKPOINT entries).
- [x] `docs/architecture.html` updated to current state: notification-
      service added to the topology, integration point #3 shown as
      genuinely bidirectional (not producer-only), the three-topic retry/
      DLQ shape shown as a flow diagram. — Evidence: §01's SVG restyles the
      `notifications` box/arrow to `ok` and adds a new Phase 5 divider row
      (kafka:notifications → notification-service → kafka:notification-retry
      → kafka:notification-dlq); badges, proven/not-built lists, and
      reproduce-yourself commands updated to match; all 5 SVG blocks
      verified well-formed XML.
- [x] `docs/build-log.md` entries appended for P5.T1–T4 and the CHECKPOINT
      review. — Evidence: six dated 2026-08-18 entries from kickoff
      generation through the final CHECKPOINT documentation sweep.
- [x] Decisions-log delta logged — the §17 amendment made before
      implementation began (already added, see top of this file), plus
      anything else that surfaces during implementation or the CHECKPOINT
      review. — Evidence: §17 amendment unchanged in substance; two new §26
      limitation bullets added (Redis hold-strategy non-transactional
      confirm/release; repo-wide died-consumer-task-only-logs pattern),
      both surfaced by this phase's code review, neither a design change.
- [x] `CLAUDE.md` self-update check run explicitly. — Evidence: the
      per-service-layering bullet extended with the no-datastore-at-all
      case; the Kafka-consumer-bounded-retry bullet extended with the
      republish-stands-in-for-DB-write generalization and the DTO-
      tightening-vs-internal-construction caution from the second review
      round.
- [x] `/pre-pr` (simplify → code-review → verify) run against the diff
      since the commit Phase 5 started from; findings self-applied. —
      Evidence: simplify (commit `70dbe11`), two code-review rounds (fixes
      in `ec18336` and `a8411c6`), verify (a Sonnet subagent's live pass
      plus this session's direct DLQ/refund-failed follow-up checks) — all
      recorded in build-log.md's CHECKPOINT entries.
- [x] Cross-doc staleness sweep: root `README.md`, `infra/README.md`,
      `notification-service/README.md` (currently a Phase-0 placeholder —
      needs a real description), `docs/report/README.md`'s chapter table,
      and any report chapter referencing the `notifications` topic as
      producer-only. — Evidence: `notification-service/README.md` already
      had a real description since P5.T1 (verified, no placeholder text
      remained); root `README.md`, `infra/README.md`, `infra/prometheus
      /prometheus.yml` (notification-service scrape job had never been
      added — fixed), and four report chapters (`requirement-gathering.md`,
      `class-diagrams.md`, `database-schema-design.md`, `testing-strategy.md`)
      updated; `docs/report/README.md`'s table updated to match all four.
- [x] This checklist itself — every box flipped to `[x]` with a one-line
      note pointing at actual evidence, not left unchecked despite
      genuinely-done work (the failure mode `CLAUDE.md`'s phase-end
      checklist item 9 exists to catch). — Evidence: this edit.

**Next:** Phase 7 — Frontend, per the locked report-first build order
(§27: P8 → P4 → P6 → P5 → **P7** → P10 → P9/P11).
