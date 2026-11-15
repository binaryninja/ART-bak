# Independent provider retry review

Scope: `artex/providers/retry.py`, its 12 authored tests, provider normalization,
HTTP transport exception boundary, pinned Norma v0.4.3 reference source, and
`server/task_llm.go`. Static reads and inert fixtures only. No upstream execution,
network requests, credentials, real model calls, or dependency installation.

## Blocking finding: refusal metadata can replay after transport failure

At review time, `_stream` buffered OpenAI `message_delta` with
`stop_reason='refusal'` while leaving `delivered=False`. If the iterator then
raised a transient transport exception, the exception branch retried the entire
request. A refusal must close the replay window even before buffered metadata is
delivered. Independent regression:
`test_retry_review.RetryReviewTests.test_refusal_metadata_then_failure_never_replays`.
The implementation owner was notified; this report does not edit the implementation.
Initial result: six independent tests, five passing and this one failing.

## Confirmed properties

- Source defaults match: Norma connect retries 3, OpenAI empty retries 2,
  exponential delays starting at 0.5 seconds with an 8-second cap. ARTEX same-
  provider retry defaults 2 and caps at 4 seconds. Counts exclude the first try.
- Shared context carries a bounded total attempt counter and deadline through
  provider/transport nesting. Both layer entries consume budget, so the bound is
  conservative and is not a count of network calls alone.
- After an event is actually delivered, transient stream failure is not replayed.
- Consumer close propagates to the current provider iterator, including when the
  first yielded event originated in the metadata buffer.
- Cancellation immediately after transport response acquisition closes that
  response instead of returning it.
- The HTTPError boundary uses only status, not request headers, prompt or response
  body. Retry code does not log or format secrets. Arbitrary injected provider
  exception text is still caller-owned; this is not a general exception sanitizer.

## Limits and integration conditions

- Unknown structured errors are not guessed transient; quota classification is
  incomplete and must be supplied before this wrapper where applicable.
- Cancellation/deadlines cannot preempt a blocking injected transport/provider.
- Transport/provider policies should be identical as documented; nested policies
  are not cross-validated, although the inherited budget still bounds work.
- Only side-effect-free model providers/transports are eligible. Billing
  idempotency, tools, profile failover and distributed retries are not guaranteed.
- No factory wiring should be enabled until the refusal regression passes.

## Fix verification

Implementation owner added sticky refusal detection before metadata buffering.
All six independent regression tests now pass unchanged. Factory review confirms
RetryTransport and RetryProvider share the same Policy, clock and wait dependencies
inside ConfiguredProvider. Existing provider test suite also passes. No additional
blocking issue identified in this bounded review.
