# Task-template JSON wire encoding

The existing authenticated `POST /api/task-templates` and
`PATCH /api/task-templates/{id}` routes require UTF-8 JSON without a leading BOM.
They no longer accept UTF-16/UTF-32 payloads through Python's implicit byte-input
encoding detection. A `charset` parameter does not enable transcoding. This
matches the alternate-encoding/BOM rejection of the original Go route's
`encoding/json.Unmarshal` calls in `server/task_templates.go`.

The 512 KiB limit is still checked on wire bytes before decoding. Invalid input
returns the existing redacted 400 response before calling the template store;
oversized input still returns 413. Valid UTF-8, ordinary JSON Unicode escapes,
and a U+FEFF character *inside* a string remain supported. The port's existing
stricter rejection of malformed UTF-8, unpaired surrogates, duplicate/unknown
fields and invalid template values remains intentional. No general Go JSON
decoder equivalence is claimed.

This is a parser correction in already-mounted routes, not a new capability.
Authentication, SQL, persistence schema, delete behavior and task execution are
unchanged. Both the explicit-store application and persisted application use the
same corrected service. No other request parser is changed.

`tests/test_task_template_json_wire.py` covers the actual application factory,
WSGI framing and original HS256/JWT authorization with synthetic credentials
and an inert SQL-capturing executor. It checks rejection without SQL calls,
valid Unicode parameter binding, missing authorization, encoding headers,
embedded BOM handling and byte-limit boundaries. These are not real PostgreSQL,
socket or rendered-browser acceptance tests. The broader suite and independent
review remain separate verification gates.

## Excessive JSON nesting

If Python's JSON decoder raises `RecursionError` for an excessively nested
request, these same POST/PATCH routes now return the existing redacted 400
instead of incorrectly labeling invalid client input as a 500. The catch wraps
only JSON decoding: a `RecursionError` from the template store still produces
the existing redacted 500, without automatic retries. Byte limits and
authentication retain their existing order, and no interpreter recursion limit
is changed. This is not a new fixed nesting-depth guarantee; the decoder's
supported depth depends on the Python runtime.

`tests/test_task_template_json_depth.py` checks real decoder failures using
bounded synthetic nested arrays/objects through WSGI and original HS256 JWT,
with zero template-store calls, plus malformed input, 413 precedence, 401
precedence, unchanged valid receipts and distinct internal store failures.
The original Go route also reports JSON decoding failures as 400; identical
Go/Python depth thresholds are not claimed.
