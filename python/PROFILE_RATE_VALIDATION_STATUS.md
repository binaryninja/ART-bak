# Profile rate-limit numeric validation

Oversized JSON integers in `rate_per_second` or `rate_per_minute` now produce the
existing redacted 400 response from `POST /api/llm/profiles`, before the mutation
coordinator is called. Previously, converting such an integer in
`math.isfinite` raised `OverflowError`, incorrectly producing a 500. Direct
`ProfileStore.save` callers now receive `ProfileInvalid` before any transaction
or SQL operation for the same invalid value.

The original Go route `pgSaveProfile` in `server/server_mgmt.go` decodes into
`db.LLMProfile`; its rate fields are `float64` in `db/config.go`, so out-of-range
numbers are decode errors reported as 400. This correction aligns that input
failure, without changing the port's existing finite/nonnegative rate policy.
Finite integers and floats, zero/unlimited, negative zero and fractional rates
retain their values and types. No operational rate cap or rounding is added.

Only the shared profile *input validator* changes. Credential handling, SQL,
coordinator implementation, provider/runtime behavior, route mounting and
runtime snapshot DTO validation are unchanged. Missing coordinators still
return 503 for valid requests; `OverflowError` from a configured coordinator
remains an internal error rather than being relabeled as bad client input.

`tests/test_profile_rate_validation.py` covers both direct-store validation and
the existing application factory, original HS256 JWT and WSGI response path.
All inputs are synthetic; executor and coordinator boundaries are inert test
doubles. It verifies no side effects for invalid numbers, valid finite
boundaries, unchanged unauthorized/missing-adapter behavior and redacted adapter
errors. No PostgreSQL, provider, socket, browser or compiled Go acceptance is
claimed. Independent correctness review and full aggregate tests are separate
verification gates.
