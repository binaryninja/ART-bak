# Explicit typed metadata read mounting

`bind_metadata_reads` extends the existing optional JWT view with exact typed
configuration, rule, judge-usage and notification-history services. At least one
service and a shared executor are required. Only supplied services are mounted;
conflicts are checked atomically, and the original application remains unchanged.
`bind_intercept_reads` retains its original signature and result type.

Judge days and immutable history selection are parsed by the same pure helpers
used by the service. Exact normalized selections are retained through nested
requests and final grant checks. An independent boolean-vs-integer selection
validation defect was fixed: invalid supplied fields return400 before grants or
SQL. History requires independent read and raw diagnostics permission; callbacks
are refreshed after the last JWT evaluation and after start_response. Revocation
withholds the body, not a fictitious replacement status after headers.

Actual isolated PostgreSQL stage102 passes three groups and49 focused tests.
Inventory support is deliberately narrow: only four exact file/owner/condition/
method/path declarations are recognized as optional. Unknown control flow, else,
extra arguments/public flags and duplicate declarations still fail closed. Sixteen
AST-only scanner tests pass; fixture code is never executed.

Frozen full suite2912 collected tests458 SQL syntax checks. Inventory184/261 unique
source routes,204 declarations,14 alternatives; counts are not behavioral parity.
No default activation, mutation, sending, delivery retry, positive timed admission
or production guarantee. See ISOLATED_QA.md for immutable evidence and limits.
