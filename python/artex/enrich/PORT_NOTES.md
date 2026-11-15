# Asset enrichment port

`Engine` ports `enrich/enrich.go`: bounded asynchronous queue, worker defaults,
five-minute per-kind/asset cooldown, best-effort DNS record upserts, HTTP metadata
upserts, title extraction, and one-MiB response cap. Public Python methods are
snake_case; `New`, `ResolveDomain`, `ProbeSite`, and `Close` aliases are provided.

## Integration and limits

- Inject an asset-store adapter implementing `upsert_ip`, `upsert_subdomain`, and
  `upsert_http_service`. Request dataclasses are defined in this package. No SQL
  implementation or graph behavior is claimed here.
- Inject a resolver implementing `query_multiple(host) -> DNSRecords | None`.
  There is no bundled DNSX equivalent yet, and DNS is a no-op without one. An
  adapter needs A/AAAA/CNAME queries, DNSX resolver selection, three retries and
  four-second timeout to achieve upstream DNS transport parity.
- HTTP uses standard-library urllib. The proxy callback is evaluated per request;
  blank means direct (environment proxies are deliberately ignored). Redirects
  are never followed. Non-2xx status responses are retained. TLS verification is
  disabled to match the upstream recording-proxy behavior. This client is not
  appropriate for authenticated/general-purpose HTTP.
- The 12-second urllib timeout is an operation/socket timeout, not Go's whole
  request deadline. A strict total deadline remains an integration gap.
- Upstream's comments mention RoE gating but its probe implementation does not
  enforce it. This port likewise requires callers to authorize targets before
  enqueueing. Nothing was probed during development.
- Shutdown is idempotent/non-blocking and in-flight work may finish. Unlike Go,
  post-close enqueue is explicitly ignored. Cooldown check/update is atomic,
  avoiding the upstream concurrent check/store race. Failed jobs still consume
  their cooldown window, matching upstream.
- Response read failures currently discard the HTTP result rather than retain a
  partial read as Go may do. No network/error-path parity beyond mocked tests is
  asserted. The queue is best effort, not durable, and close can abandon jobs.

## Validation

`PYTHONPATH=python python -m unittest discover -s python/tests -p test_enrich.py -v`

Ten isolated tests cover records/upserts/errors, helpers, cooldown boundaries,
worker deduplication, queue drops, bounded HTTP reads and cleanup, live proxy
selection, redirect prevention, and HTTP-error responses. All transport calls
are mocked; upstream code was never executed and no dependency installed.
