# notification-service

No DB (decisions-log §17 amendment, 2026-08-18). Kafka #3 consumer for the
`notifications` topic (booking-confirmed, payment-confirmed, refund-failed —
decisions-log §7 point 3), "delivers" via structured log output only (§19 —
no real email provider). Implements a hand-rolled retry/backoff/DLQ ladder:
a failed delivery republishes to `notification-retry` with increasing
backoff, and after `retry_max_attempts` republishes to `notification-dlq`
rather than being silently dropped.

Scaffolded in Phase 5 (`docs/phases/phase-5-kickoff.md`); see that file and
`decisions-log.md` §17 for the full design (three-topic shape, the
attempt-carrying `RetryEnvelope`, and the `simulated_failure_attempts`
demo/test instrument used to exercise the ladder against a real running
stack, since this service has no real external delivery dependency capable
of a genuine failure).
