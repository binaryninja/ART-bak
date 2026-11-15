# Injected provider adapters

Implementation reference: official `Autumn-27/norma` tag `v0.4.3`, fetched
read-only, especially `llm/{types,provider,openai,anthropic}.go`; ARTEX
`agent/provider.go` supplies the upstream configuration. No upstream source was
executed or installed. This is a Python interoperability implementation, not a
claim of complete SDK parity or validation against live provider services.

## Contracts

- `Provider(Config(...), transport)` implements `complete(context, request)` and
  `stream(context, request)`, compatible with `llmpool.Pool` and `llmrec.Recorder`.
- Completion returns `(message, stop_reason, usage)`. Messages and blocks use
  the shared lower-case dictionary schema. Tool inputs are decoded JSON objects.
  The message also includes the provider's returned `model`, falling back to the
  configured model. Usage has input/output/cache-read/cache-write counts.
- Streaming returns dictionary events with Norma's normalized `type` names.
  Tool events also include `index` and `tool_id` so interleaved calls remain
  attributable. An assembler must honor those fields, not concatenate all calls.
  Only terminal `[DONE]` / `message_stop` marks successful EOF.
- `ContextBoundProvider` bridges `sidequestion`'s `complete(request)` contract.
  Pass a context factory carrying the appropriate session/cancellation state.
- Transport is a required callable `(context, url, headers, json_bytes, stream)`
  returning `Response(status, body, close_callback)`. Completion bodies are bytes
  or strings; streaming bodies may be iterables of byte chunks. The transport
  owns HTTP policy, timeouts, proxy routing and cancellation while blocked.

The core adapter has no implicit networking or credential discovery. An optional
stdlib `HTTPTransport` now exists in `http.py`: explicit origin allowlists, TLS
verification, no redirects or environment proxies, bounded responses and cooperative
cancellation. Explicit retry layers exist in `retry.py` and are composed by
`ProfileFactory`; no tool execution or rate limiter is supplied. Hosts must choose
transport policy and protect credentials. Tests use injected transports and
placeholder credentials, not live provider validation.

## Supported behavior

- OpenAI-compatible Chat Completions and Anthropic Messages serialization,
  tools, textual tool results, thinking/signature preservation, stop sequences,
  temperature, output-limit field choice and Anthropic static-prefix caching.
- OpenAI reasoning aliases, refusal text and an explicit normalized `refusal`
  stop reason (also preserved on the completion message). Refusal takes priority
  over contradictory tool-call finish reasons; it is never retried to avoid it.
  Stream consumers must buffer the complete turn before considering tool
  execution: a refusal may arrive after an earlier tool fragment. Tool calls
  and other finish reasons are normalized.
- Anthropic cache usage folds cached tokens into total input, matching Norma.
  Streaming input is counted only at message_start, output at message_delta.
- SSE framing tolerates byte boundaries, CRLF, comments and multiple data lines;
  limits individual lines/events to 8 MiB. Invalid JSON/UTF-8 is surfaced.
- Non-2xx failures expose an HTTP status, not credentials or response contents.
  Provider structured errors and protocol failures remain separate. Mid-stream
  failures propagate; the pool must never retry after any normalized event.
- Responses close on normal completion, parse failure, HTTP failure and explicit
  iterator close. A consumer abandoning a generator must close it explicitly.

## Deliberate differences and remaining gaps

- Request history is strictly paired: every tool call must have exactly one
  result in the next user message, with no duplicate IDs or orphan results.
  Malformed histories raise `ProtocolError` rather than silently dropping calls,
  inventing tool output, or inserting fabricated reasoning placeholders.
- Anthropic replayed thinking is emitted only when thinking is explicitly
  enabled and its original signature exists. Norma's configuration-only branch
  considers any nonempty mode enabled, including `disabled`; this port does not
  preserve that inconsistent behavior.
- No Responses API, multimodal content, redacted-thinking blocks, or provider
  extensions beyond documented fields. Unsupported blocks fail explicitly.
- Explicit bounded establishment/empty-response/same-provider retry layers now
  exist in `retry.py`; they do not replay after delivered events. Rate limiting,
  ARTEX quota-aware routing and session-header configuration remain unavailable.
  Cooperative transport deadlines are not hard real-time cancellation guarantees.
- Raw request/response `llmrec.Capture` is not wired at this layer yet. Recorder's
  normalized messages and usage work; an injected transport can use its context
  capture to record raw bytes and each attempt without including auth headers.
- Fragmented tool arguments and parallel indexed calls are supported. Tool-name
  fragments arriving after the start event fail rather than mislabel a tool.
- Live API interoperability, cancellation during blocking transport reads, all
  provider-specific event variants, and network security policies are unverified.
