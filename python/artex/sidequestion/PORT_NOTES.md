# Side-question Python port

Ported from `sidequestion/{capture,request,service,context}.go`. Standard library only.

Implemented: parent keys; credential-free model identity; deep-copied checkpoint
capture and terminal host-reminder preservation; bounded successful exchange
history; independent output/input budgets; English isolation and summarization
prompts; immutable request assembly; synchronous/streamed answers and partial
failure output; rolling ordinal-based history summaries; paired tool-call/result
message groups; snapshot-summary cache; UTF-8-safe bounded summarization; 12-call
summary cap; one strictly-smaller overflow recovery; actual provider usage
aggregation; cooperative cancellation; generation-checked persistence callback.

## Integration contracts and gaps

- Dictionary messages use `role`, `content`, and block `type` (`text`, `tool_use`,
  `tool_result`). Providers implement `complete(request) -> (message, stop, usage)`
  and `stream(request) -> event dicts`. No live provider, transport, or model is
  included. A provider exception cannot expose usage unless an adapter supplies
  it separately; partial stream usage is retained in `AnswerError.answer`.
- The token estimate is conservative UTF-8 serialized block sizing with 4/3
  allowance, **not exact norma compaction parity** or model tokenization. Summary
  chunks are shrunk further if this estimate would exceed the input budget.
- `Capture` is explicit; Go QueryDeps/context Bind/Attach hooks, routing-pool
  integration, and automatic post-completion checkpoints are not wired. Caller
  must capture only concrete main-agent calls, not auxiliary summaries.
- Messages must already be API-normalized. No replacement for norma
  `MessagesForAPI` filtering or signed-thinking normalization is bundled.
- Tools and tool_choice are explicitly stripped from outgoing requests (stricter
  than the source request clone); structured historical tool evidence is retained.
  The service never executes tool requests or writes the main transcript.
- `Replay.load(after)` must return bounded ascending completed exchanges restricted
  to ordinals preceding this admitted request. `Replay.save(memory)` must reject
  stale request generations after clear/delete/cancel. In-memory validation rejects
  invalid cursors, but database transactions, pagination bounds, archive persistence,
  request admission, HTTP/SSE routes and concurrency limits are not implemented here.
- Cancellation is a callback checked between operations; it cannot preempt a blocked
  synchronous provider call. The caller/provider adapter must enforce the shared
  deadline (the source deployment uses 120 seconds).
- Snapshot hash keys are Python JSON keys and are not interchangeable with Go cache
  keys. Exchange is the minimal replay view, not the full API/database exchange row.
- No claim of full application, wire-format or upstream Go-test parity.

## Isolated validation

`PYTHONPATH=python python -m unittest discover -s python/tests -p test_sidequestion.py -v`

15 tests pass, with deterministic local fake providers only. Covers capture
isolation, budgets, completed-history filtering, tool grouping, streaming fallback
and truncation, replay cursors and stale-generation save rejection, rolling history,
snapshot cache reuse, summary rejection, cancellation, bounded recovery and call
caps. No source Go was executed; no network, installation, credentials or real
model calls were used.
