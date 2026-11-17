# OpenAI response and event parsing

The OpenAI branch of `parse_response` and `_openai_events` accept the existing
text/function subset of Chat Completions. Tests pass JSON objects and frame
iterables directly; they do not construct a transport or contact a provider.

The reference is [Norma v0.4.3 `llm/openai.go`](https://github.com/Autumn-27/norma/blob/v0.4.3/llm/openai.go),
pinned by this repository's `go.mod` and `go.sum`. Its completion parser turns
whitespace-only function arguments into an empty object. Its frame parser emits
both content and refusal when the same delta contains both. These two mappings
are now preserved here. The whitespace set matches Go `strings.TrimSpace`;
Python-only whitespace U+001C–U+001F remains invalid JSON.

The Python adapter also enforces its documented normalized-content contract:

- Content, refusal and every reasoning alias must be strings or null, even if
  a malformed value is falsey or another alias takes precedence.
- Choices, messages, deltas, tool-call arrays and function objects must have the
  expected shapes. Invalid supported fields raise `ProtocolError` without
  embedding provider content in the exception message.
- Explicit tool types other than `function` are unsupported. Missing/null/empty
  type remains accepted for existing compatible gateways. Completed function
  calls require nonempty string IDs and names. Completion arguments retain the
  existing decoded-object extension and must resolve to a JSON object.
- Stream tool indexes must be nonnegative integers (not booleans); missing index
  retains the existing default of zero. IDs, names and argument fragments must
  be strings or null. Once an index has an ID, a different ID is rejected before
  emitting fragments under the new identity. Repeating an identical ID is valid.

The typed rejection is an intentional Python difference: the Go frame parser
silently omits malformed frames, and its raw completion argument representation
is more permissive. This adapter continues to surface protocol failures.

Existing first-choice selection, reasoning-alias priority, completion
content/refusal fallback, refusal stop priority, queued arguments before tool
identity, parallel indexed calls, and terminal event behavior remain unchanged.
Usage still comes from the latest cumulative snapshot; this change does not
alter `_usage`, `_frames`, Anthropic, serialization, transports or retry behavior.

This is not a complete wire-schema or lifecycle validator. Unknown metadata is
still tolerated; secondary choices are not projected. Stream arguments remain
partial strings and are not required to form complete JSON at this layer.
Post-start tool-name fragments remain unsupported. Full multimodal, Responses
API and vendor-extension support, live interoperability and executed Go
comparison are outside this bounded fixture coverage.
