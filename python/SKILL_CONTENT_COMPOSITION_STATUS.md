# Optional content family in skill-read composition

`bind_skill_reads` now accepts paired optional `content_service` and
`authorize_content` arguments. Existing three-family callers remain supported;
omitting both leaves content paths with the original application. Supplying only
one fails before any binding. Listing permission never enables content reads.
No default application wiring or source-reader behavior changes are included.

The fourth overlay is constructed against the exact same original JWT app, not
stacked on the listing overlay. Content keeps its nested-path dispatcher,
all-method conflict detection, independent request/source/current/global grants,
sanitized errors and final JWT/snapshot/pathname-receipt disclosure checks.
The composer adds exact content delegate class/call/check/constructor and binder
anchors and same-original checks, including post-header selected-view checks.
Unselected families do not run policies or I/O. Original routes remain unchanged
on failure, including a conflict discovered in the final content binder.

Eleven new composition tests and all 194 skill-focused tests pass. They cover
four-family reachability and isolated permissions, nested encoded paths, method
behavior, omitted/partial configuration, JWT denial before I/O, final-family
non-GET conflict atomicity, pre-bind method/binder replacement, selected-view
replacement before/after headers, post-header file/permission changes, final
outer JWT expiry and unrelated-family retirement. These tests use disposable
POSIX files, inert database adapters and real WSGI/JWT without a listener.
Independent read-only review cleared this implementation. Additional probes
confirmed omitted content preserves an existing original nested route; foreign
base substitution refuses; post-header delegate/source/request/current changes
withhold the body; and an invalid fourth service leaves routes and grants untouched.

PG+FS158 passed six isolated integration groups and all 194 skill-focused tests:
1. Nested/encoded paths and post-header file/inode/ancestor/root changes or JWT expiry.
2. Real PostgreSQL hot/cold missing totals and usage, filesystem listing and
   content through one genuine-JWT composition, with original/unauthenticated
   and unrelated-route behavior preserved.
3. Independent family/request/source/current grants and unrelated retirement.
4. Final outer-callback JWT expiry withholding bodies for all four families.
5. Post-header grants and selected-family replacement, plus atomic conflict refusal.
6. Pre-bind call/check/constructor and binder substitutions for all four families,
   foreign originals and selected-base replacement refused before reads.
Deep non-GET content route conflicts also refused without router mutation.

Report: `/workspace/shared/artex-qa/skill-content-composite-158/REPORT.md`.
Manifest SHA256: `df32b6a45511e197fee113ffc48a54659d41a12ad5a380621fe79e1eeae3a828`.
Composite SHA256: `7a03316e2a6b71df5ca3876071457110075f6c8bf7ccc892a4c3e8d65a145726`.
The snapshot combined a copied index with explicit current overlays, so this is
frozen-runtime evidence, not exact staged-tree acceptance. Manifest entries and
source hashes matched after testing. The run used fresh disposable PostgreSQL 17
and temporary files in an isolated UID1000 namespace, loopback only, synthetic
credentials/rows and no host workspace/home access. Sources and index were not
changed. PG+FS155 and FS157 remain earlier, narrower evidence.

Acceptance used component WSGI calls, not a real HTTP listener/browser or default
wiring. It ran the focused skill suite, not the full repository suite or a SQL
syntax run. Deterministic callback mutations do not exhaust possible interleavings.

Existing cooperative lifecycle/callback/writer constraints still apply. This
finite observer sequence is not an arbitrary-mutating-callback fixed point or
atomic filesystem snapshot. Module-load implementation anchors do not defend
an already-compromised import environment or arbitrary private-state writes.
There is no claim of browser/frontend or whole-application parity, and no
staging or publication is performed by this slice.
