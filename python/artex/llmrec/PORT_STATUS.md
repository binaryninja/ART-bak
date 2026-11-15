# LLM recording port

Implemented an independent standard-library Python port of `llmrec.go` and
`capture.go`: explicit task/worker context, exploration/session parsing,
request normalization (including thinking overrides and tool names), streaming
usage folding, atomic completion metering, gated body recording, synchronous
sink writes, provider error propagation, and thread-safe per-attempt raw capture.
All new prose is English. Original source is untouched.

Validation: `PYTHONPATH=python python -m unittest discover -s python/tests -p test_llmrec.py -v`
passes 12 offline tests using in-memory providers, sinks, and byte streams. No
upstream Go code was executed and no network requests were made.

## Integration boundaries

- Provider adapters must implement the documented synchronous Python protocol:
  `stream(context, request)` yields mapping/object events with string types
  `message_start`, `message_delta`, `text_delta`, `thinking_delta`; exceptions
  represent errors. `complete(context, request)` returns message, stop, usage.
- Production PostgreSQL adapter and provider/transport wiring are not supplied.
  The injected sink implements `insert_llm_usage` and `insert_llm_record` with
  dictionaries. Cache token keys use Python usage names, so a Go-schema adapter
  must translate these into `cache_read` and `cache_write` columns as needed.
- Capture uses bytes rather than decoded strings to preserve non-UTF8 wire
  content. A database adapter must choose binary storage or explicit encoding.
- Raw recording includes full request/response data, with no credential or
  content redaction, matching source behavior. Production enablement needs a
  deliberate retention/access policy. Transport headers are not captured.
- Early consumer exit must explicitly close the iterator (e.g.
  `contextlib.closing`). Python `break` alone does not ensure timely cleanup.
  An iterator never started has made no provider call and writes no rows.
  Partial-consumer close has status `ok` unless context carries an `error`,
  matching the source distinction between early stop and cancellation.
- Async providers and context cancellation/deadlines require an adapter. This
  package does not create threads, schedule calls, or connect to any service.
