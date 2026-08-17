# Report drafts

Continuously-drafted source material for the 40-page capstone report — per
`master-development-plan.md` §16 ("Report-evidence capture map") and the
per-phase DOCUMENT step: each phase drafts the report section its evidence
feeds, *while the material is fresh*, so P11 is an assembly + formatting pass
into the institution's template, not first-drafting from scratch.

**This is source material, not the final formatted document.** Plain
Markdown here; copy/adapt into the actual submission template (Times New
Roman, 14pt headings/12pt body, per-chapter figure/table numbering) at P11.

**Every status label follows CLAUDE.md's integrity rule** — Planned →
Implemented → Tested → Deployed → Measured → Verified. A claim in these
drafts carries the label it actually earned through real execution in this
repo, never an aspirational one.

## Chapter status

| Chapter | Status | Fed by | File |
|---|---|---|---|
| Project Description | Draft (partial — covers P0-P3) | P0 topology figure, architecture recap; P1 event management, P2 browse/search, P3 booking + dual hold | `project-description.md` |
| Requirement Gathering | Draft (partial — Event/Search/Booking/Payment/Cancellation roles) | P1.T5 (roles table), P2.T1 (publish-step amendment), P1 addendum (venue/seat-map write API), P3.T6 (booking roles table + delete-rule amendment), P4.T3 (`/pay` roles table), P6.T1 (`/cancel` roles table); notification delivery still to come (P5) | `requirement-gathering.md` |
| Class Diagrams | Draft (partial — Event Service + Search Service + Booking Service + Payment Service) | P1.T4, P2.T2–T4, P3.T1/T3–T6 (Booking Manager/Repository + TicketHoldStrategy), P4.T2–T6 (Payment Manager/Repository/Producer + Booking's `pay_booking`/`PaymentOutcomeConsumer`/`confirm_hold` additions), P6.T1–T3 (`cancel_booking`/`release_booking`/`EventRepository`/`BookingCancelledProducer`; `refund_payment`/`NotificationProducer`/`BookingCancelledConsumer`) | `class-diagrams.md` |
| Database Schema Design | Draft (partial — `event_db`, seat-map docs, ES index, `booking_db`, `payment_db`) | P1.T2 (ER + textual), P2.T2 (ES mapping), P3.T1 (`tickets`/`bookings` ER + textual), P4.T1/T2 (`tickets.price_cents` + `payment_db` ER + textual), P6.T1/T2 (`booking_db.events` + `payments.stripe_refund_id`/`refunded` status) | `database-schema-design.md` |
| Feature Development Process | Draft (Measured) — P8 benchmark complete | P8 benchmark (measured), P3 hold-strategy design | `feature-development-process.md` |
| Testing Strategy | Draft (partial — 4 tiers: unit/integration, Kafka+ES, adversarial, P3 test-first concurrency + 2-pass review, P4 self-verification + live-testing-caught bugs, P6 self-verification + real Kafka-testcontainer infra bug found+fixed) | P1.T6 (`testcontainers` first use), P2.T3/T5 (Kafka consumer redelivery + eventual-consistency testing), pre-Phase-3 adversarial pass (8 rounds, 5 bugs found+fixed), P3.T3–T5/T7 (test-first `TicketHoldStrategy` contract + race proofs, both real strategies), P3 checkpoint (routine + dedicated adversarial `/code-review`, 2nd pass catching bugs in the 1st pass's own fixes), P4.T4/T7 (webhook + charge idempotency test-first, live-testing-caught idempotency bug + bind-param regression), P6.T4 (first real-Kafka-broker transport test in the project, surfaced a 3-phase-old broken-fixture bug); every later phase's suite | `testing-strategy.md` |
| Deployment Flow | Not started — blocked on P10 | P10.T1–T4 | — |
| Technologies Used | Draft (partial, running list) | every phase's tech, incl. P2's Kafka integration + Elasticsearch, P3's Redis + APScheduler, P8's Prometheus/Grafana + Python asyncio load harness | `technologies-used.md` |
| Conclusion (Limitations/Future Work) | Not started — drafted last, P11.T3 | decisions-log §26 pull-list | — |
| Abstract | Not started — written last, P11.T1 | whole report | — |

Update this table every phase alongside the chapter files — it's the one
place that shows report progress at a glance without opening every file.
