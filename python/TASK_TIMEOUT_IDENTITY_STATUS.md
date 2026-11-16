# Per-task timeout configuration identity

TaskRuntimeAssembly now validates exact nonnegative INTEGER-range timeouts for
provisional create options and persisted tasks. Booleans, strings, negative,
missing/invalid persisted values and conflicting top-level/options snapshots are
rejected. Persisted identity reads the actual TaskStore under the existing
TaskProviderResolver task-read authorization/current/original guard contract,
including post-guard authorization. Caller snapshots cannot override the row.

Timeout is checked before and after role/model selection. A positive duration
requires the exact explicit timing policy and cleanup configuration, and enters
the durable configuration digest. Provisional and persisted forms of the same
duration/profile configuration match; changed duration invalidates the prepared
revision. Zero keeps the previous payload/digest bytes, including default mode.
Policy construction revalidates frozen policy fields instead of trusting type
alone after host-side mutation.

Seven dedicated inert tests and 59 dependent runtime tests pass. The shared
provider test fixture was narrowly corrected to include `id=5` and
`timeout_seconds=0`, fields returned by actual TaskStore. No production fallback
for missing persisted timeout was added. Three independent regressions pass.
Final post-guard grants are followed by strict comparison of captured row
identity/exploration/timeout with the same returned object, rejecting demonstrated
alias mutation. This does not make multiple SQL reads atomic against unrelated
external database writes; later bootstrap/engine checks remain necessary.

This is configuration binding, NOT timed admission. EngineRunner, BootstrapHandle
and the engine SQL positive-duration gates remain closed. Early proven-goal
completion/lease release and tracked finalizer invocation inventory during
shutdown remain blockers from NATURAL_TIMED_ADMISSION_PLAN.md. No timer,
observer registration, schema installation or final action is enabled here.

Exact frozen checkpoint:2861 collected tests457 SQL syntax checks pass.
Isolated PostgreSQL stage99 passed six integration groups and26 focused tests;
see ISOLATED_QA.md for manifest and precise natural zero-timeout/read acceptance.
Public positive-duration admission is still refused.
