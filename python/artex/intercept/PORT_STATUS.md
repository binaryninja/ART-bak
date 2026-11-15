# Interception domain port

Implemented: priority-ordered matching, enabled-tool defaults and invalidation,
strict verdict parsing (English plus legacy wire text), UTF-8 limits and digests,
v4 current-call review envelope with explicitly selected human background,
injected model reviewer, pending approval lifecycle with cancellation and atomic
human/timeout decisions, insertion/registration race handling, guard adapter.
Tests use an in-memory mock only. No tool, model, upstream server or network call
is executed.

Intentional safety differences: configuration/regex/storage loading errors block
instead of silently skipping rules or disabling interception. Model failures
DEFAULT TO DENY (Go defaults to allow); callers can explicitly select another
failure policy. Python regex is not Go RE2: patterns must be trusted and reviewed
for complexity. English explanation labels are new; legacy labels are accepted
for reading saved verdicts. Digests use Python JSON canonicalization and are not
byte-for-byte compatible for every Go number/escape representation.

Remaining: SQL-backed Store implementation and transaction validation; HTTP
approval routes and conversation persistence; configuration KV read/write API;
full translated default judge policy/boundary prompt and provider timeout adapter;
trace/result correlation and audit persistence from trace.go; concurrency/load
integration testing against the actual database. No full runtime/security-boundary
parity is claimed. The reviewer must enforce timeouts externally. Guard integration
must enforce cancellation for all calls, not just asks. Explicit empty enabled tools
and absent reviewer preserve upstream opt-out behavior.
