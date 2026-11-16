# Optional notification metadata PATCH composition

`bind_notification_metadata` composes an exact original WSGI application, typed
metadata service and independent global write grant. It leaves the original
application unchanged. Its only intercepted method/path family is PATCH
`/api/notify/channels/{id}`. There is no default activation, channel creation,
credential editing, deletion, test send, delivery retry or notification transport.
The conservative Router.add inventory does not yet count this explicit wrapper.

Original JWT, service/store resources, request proposal and write policies are
pinned. The original normalized proposal reaches actual store authorization.
The final write gate follows callback-bearing source checks immediately before
each UPDATE and before transaction exit; only pure original identity/proposal
checks follow it. This is a finite cooperative boundary, not an atomic fixed
point for arbitrarily mutually mutating policies.

Metadata and disable transitions share one transaction. Kind/config are never
read or changed by the product path. Lost commit acknowledgments report unknown
outcome without retries. After headers, late revocation aborts body delivery;
it cannot retract a status already sent. No rollback is claimed after commit.

Independent review and 29 focused tests pass. Stage129 actual PostgreSQL/JWT
acceptance passes four groups, including both preserved late-current regressions:
pre-UPDATE revocation executes no UPDATE; pre-commit revocation rolls back the
tentative update. Earlier stage126 evidence was insufficient for those cases.
See ISOLATED_QA.md for immutable manifest and isolation details.
