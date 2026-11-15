# LLM pool port

Ports `llmpool/pool.go`, reusing existing `artex.health.Registry` and
`artex.failover` instead of duplicating breaker state or error classification.

Implemented: ordered profile chains; contiguous equal-rank round robin;
thread-safe 64-bit counter; context-window and cooldown filtering; head fallback;
UTF-8 request-size heuristic; completion retries; streaming retries only before
any event; hard/transient health updates; exhaustion with chained last error.

Adapter contract: providers expose synchronous `complete(context, request)` and
`stream(context, request)` methods, returning results/events and raising exceptions
rather than Go error tuple values. Context may expose `err()`; dedicated
`artex.failover` cancellation exception classes also suppress retries. Lowercase
request mappings/objects are accepted; block `input` must be raw JSON text/bytes,
not a decoded dictionary. All output values are opaque to the pool.

Gaps: no Norma SDK adapter, live model calls, asynchronous provider bridge,
profile DB loading, recorder wrapping, telemetry/log integration, or service
wiring. The synchronous pool cannot interrupt a provider blocked in I/O; provider
adapters must implement cancellation. Python asyncio cancellation (a BaseException)
escapes directly and is not retried. Provider iterators are closed on exit as
Python resource hygiene; a faulty close() can propagate its exception. Registry
persistence hooks require the application to wire its database. Existing
trim_error deliberately drops partial UTF-8 code points at the byte cutoff.

Verification: 13 isolated unittest cases, mocked providers only. No Go code,
upstream scripts, downloaded dependencies, or network operations executed.
