# Stream usage accounting

Normalized Python provider usage is cumulative within one stream. Each of
`input_tokens`, `output_tokens`, `cache_read_tokens` and `cache_write_tokens`
reports the total known so far. `message_start` commonly carries input/cache
counts; later `message_delta` events may repeat or increase output totals.
OpenAI normally reports its totals in a final usage event. Missing or zero
fields in a later snapshot must not erase a previously reported count.

Both `Recorder.stream` and `PostgresRecorder.stream` retain the greatest observed
nonnegative integer for each counter. For output snapshots `3, 8`, the metered
total is `8`. Repeating `8`, reporting a lower snapshot, or omitting that field
does not add more cost. Counts remain local to one iterator; separate calls
still produce separate records. Only usage attached to `message_start` and
`message_delta` is folded.

This matches the existing `StreamingCompletion` contract in
`agent/streaming.py`, whose usage callback receives increments between observed
maxima. Provider events are passed through unchanged. The checked-in Go
`llmrec.go` uses additive folding; that assumption does not match the Python
adapter's repeated cumulative snapshots, so this is a deliberate consumer
correction rather than a provider-protocol change.

`Usage.add` and both `complete()` methods keep their additive behavior. The
stream fold applies maxima only to normalized nonnegative integer counters;
unsupported values retain the earlier additive handling and existing error or
settlement validation. In particular, `max(0, -1)` or `max(0, 0.0)` must not
silently make invalid durable usage acceptable. This does not add or claim a
general provider-event validator; `StreamingCompletion` retains that role.

The recorders keep their existing distinct lifecycle contracts. Compatibility
recording logs sink failures, and early close is `ok` unless its context carries
an error. Durable recording preserves its explicit binding, bounds, cancellation
statuses and atomic settlement/error behavior. Already observed usage survives
failure or explicit close; unobserved usage is never estimated. A generator that
has not started invokes no provider and records no usage. Consumers must still
close partially consumed iterators.

`tests/test_llmrec_stream_usage.py` covers both implementations with fake
providers, in-memory sinks and a fake executor that only collects query objects.
It also feeds synthetic frames through the unchanged provider parsers and
`StreamingCompletion`, checking callback increments and the existing token cap.
It opens no database, network connection or runtime host.

```sh
PYTHONPATH=python python -m unittest discover -s python/tests -p 'test_llmrec*.py' -v
```
