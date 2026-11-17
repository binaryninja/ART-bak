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
