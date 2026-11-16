# Follow-up findings and coverage disclosure correction

The earlier inner findings correction did not cover every caller. Focused review
confirmed an outer fixed-read findings request callback could revoke source access
after the inner service had validated its response. Direct coverage store and
fixed coverage HTTP reads also needed checks after lifecycle-guard exit.

The outer findings callback now participates in the canonical inner validation
chain, before the exact source set is rechecked. It no longer executes after a
validated response returns. Direct/fixed coverage readers recheck their exact
recorded source and connector grants after guard exit. Neither correction adds
SQL queries, nested guards, mutation permission or inferred global access.

Independent review retained the original counterexamples: two coverage guard-exit
cases, three outer findings cases and the earlier two inner findings cases. The
focused combined suite passed 32 collected tests. The frozen correction-only
snapshot passed 2577 test executions and parsed 435 unique authored SQL statements;
route registration coverage remains 180/261, not runtime or behavior parity.

These corrections do not establish safety against arbitrarily self-mutating
policy callbacks, production readiness or a completed port. New goal/coverage
composition and assessment-journal development are excluded from this checkpoint.

Disposable PostgreSQL stage86 passed two groups/six cases plus seven exact
regression tests. Full JWT outer findings source revocation and callback
replacement withheld data; a positive read succeeded. Direct coverage guard-exit
revocation raised CoverageDenied; fixed HTTP guard-exit revocation returned403
without aggregate data, while its positive fixture returned denominator1.
Immutable manifest:
`fe1dcaf1c28a573143b0ce50783ef504c43b8e925eded2e6b0c0df10177ed995`.
