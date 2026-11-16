# Optional authenticated skill usage composition

New `server/skill_usage_setup.py` exports
`bind_skill_usage_reads(application, *, usage_service, authorize_global)` returning
an immutable `SkillUsageReadBundle(application, usage)`. Exact original WSGI app,
Router/AuthService, typed service/reader, resource/method/callback identities are
required. Constructor invokes no policy or SQL. Original application remains
unchanged; a protected real Router declaration mounts GET only when explicitly
bound. Existing literal or mixed-parameter GET/HEAD overlaps fail before mounting.
No generic task resolver, report route, filesystem or execution capability.

Each request gets an exact derived service sharing the original reader, with
original request authority plus JWT and independent global permission composed
into its actual source disclosure checkpoints. The exact parsed selection object
and original request/body/header snapshot are retained through response release.
Source errors retain their status. Successful responses repeat validation before
and after start_response; revocation after headers raises without releasing the
body. No second SQL query, fabricated rows, or public prepared capability exists.
Original source modules are unchanged.

Final finite ordering is original JWT, source/current/original request/outer
checks, final original request and outer permission, then pure original pins.
This is not a fixed point for arbitrary mutually revoking callbacks. It does not
claim authentication remains valid after bytes have been released or add a live
listener. Original app handles unrelated routes. No registration by default.

Six authored wrapper tests and fourteen standalone owner/reviewer tests pass.
Independent wrapper review and real PostgreSQL composition acceptance pending.
No route inventory regeneration or count claim yet.

## Isolated acceptance

Stage144 passes two fresh PostgreSQL/JWT groups and 26 focused tests, including
original-app absence, bounded positive selection, HEAD refusal without SQL and
post-header global/request/source/current/JWT changes withholding the body. See
ISOLATED_QA.md for immutable evidence; no default activation or skill execution.
