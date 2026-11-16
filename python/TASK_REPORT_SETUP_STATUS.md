# Fixed-scope report WSGI composition

`build_task_report_application(application, service)` in
`server/task_report_setup.py` explicitly wraps one actual WSGIApplication and one
actual TaskReportService. The original AuthService.authorized JWT method, auth
resources, router/route inventory, transport body cap, service/store methods,
executor, original ReportScope and policies are pinned. Construction makes no
policy calls or SQL reads and does not change the original app. No default
factory/route, task resolver, active fallback or inferred global access is added.
A real protected `Router.add('GET', '/api/report', ...)` declares the overlay.
Conflicting original GET/HEAD routes are refused before composition.

The service's compatible `handle` delegates to `prepare`. Preparation issues an
exact private-recorded PreparedReport tying original response bytes/headers/status
to its request-local selector and original independent grants. Copied or fabricated
receipt objects and substituted responses cannot be disclosed. A generic caller
closure or success callback is not accepted as receipt authority. Validation
performs no repeated SQL or rendering.

The wrapper verifies disclosure before transmission and after start_response.
Original JWT validation is a temporal observation before the final source/global/
request-grant refresh; only pure request, scope, resource, response and JWT-owner
pins follow. This finite ordering catches the reproduced JWT-clock-revokes-data
case. It is not a fixed point or atomic promise against arbitrary mutually
mutating callbacks. Policies must be cooperative read-only authorization checks.
After headers have been emitted, revocation aborts body transmission rather than
pretending a replacement HTTP status can be sent. Independent global asset-count
permission remains mandatory; JWT alone grants no report data access.

26 owner/standalone/independent report tests pass locally. Unchanged independent
regressions cover the final JWT clock revoking the global count grant and an
int-to-equal-float scope mutation after headers. Those fixes are awaiting fresh
independent clearance. Tests use real JWT/WSGI and inert SQL; actual PostgreSQL
wrapper acceptance is separate and pending. Standalone report acceptance and the
unrelated timed-retirement pipeline do not substitute for this wrapper evidence.

## Actual PostgreSQL acceptance

Stage132 passes five original-schema PostgreSQL/JWT groups and 26 focused tests,
including the preserved late-JWT data revocation and equal-valued scope type
regressions. See ISOLATED_QA.md for the exact immutable manifest and limitations.
No default route or active-task fallback is enabled.
