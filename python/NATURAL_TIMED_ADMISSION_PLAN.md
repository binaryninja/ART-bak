# Natural opt-in timed HTTP admission: remaining work

Source-only inventory after the manual finalization candidate. No gates changed.
Existing component tests mutate prepared pins explicitly; they do not establish
natural HTTP admission, real positive engine execution or production readiness.

## Actual gate and configuration inventory

| Current path | Existing behavior | Required opt-in replacement |
| --- | --- | --- |
| `server/tasks.py:parse_create_request` | Parses integer timeout, clamps negatives to zero, passes TaskCreateOptions | Preserve source input semantics; enforce PostgreSQL INTEGER upper bound before effects. No route may independently authorize timed execution. |
| `db/tasks.py:TaskStore.create` | Persists timeout and creates task/exploration/origin atomically | Keep existing creation; no duplicate graph bootstrap. Add coherent input upper bound if not enforced above. |
| `agent/engine_runner.py:_prepare`, bootstrap source/options loop | Rejects every nonzero timeout, existing deadline, inherited sources, parent_ref, intercept rules | Permit positive bounded timeout only with an exact code-owned installed timed admission configuration. Keep other unsupported fields rejected, and retain rejection for ordinary runners. Validate provisional options before task INSERT and persisted values before admission. |
| `agent/engine_runner.py:_prepare`, initialized identity check | Rejects nonzero persisted timeout even when bootstrap is selected | Narrow positive branch to fresh bootstrap mode with same pinned timed configuration; keep initialized/reload/revival paths rejected. |
| `agent/bootstrap_handle.py:__init__` | Rejects positive timeout and seed_first_intent; stores timeout in private immutable tuple | Allow positive only through the same exact timed setup capability, never a standalone flag or arbitrary callback. Preserve timeout pin and all seed rejection. |
| `agent/bootstrap_setup.py` | Constructs EngineRunner and BootstrapHandle without timed admission configuration | Forward one code-owned configuration/capability through both constructions, not two booleans. Validate before startup inventory reads and before any create. |
| `agent/task_runtime.py:_select` / bootstrap_configuration | Digest includes models/prompts/catalog/limits/policy and timing enabled+revision, but not each task timeout | Include canonical timeout in opted-in durable configuration for both provisional options and persisted task selection. Preserve zero/default digest compatibility where possible. Otherwise same model/policy digest can prepare different timeout tasks without durable timeout identity. |
| `agent/bootstrap.py` | Journal hashes supplied configuration plus task description/goal; no timeout column | Feed timeout-bound configuration digest into existing journal. Do not pretend the current journal already independently binds task timeout. No default schema migration is required for this identity improvement. |
| `agent/task_timing.py` / post-prepare binding | One original owner/store/kernel, role wrappers, same clock; zero creates no kernel | Retain these checks. Positive bind must now arise from actual immutable admitted configuration rather than fixture mutation. Goals remain unwrapped and unstamped. |
| `agent/engine.py:_snapshot` | SQL explicitly requires `timeout_seconds=0` | Parameterize exact constructor-owned timeout only for the authenticated timed binding. Before stamp require original prepared owner and NULL first clock; after stamp verify original lease/clock and nonexpired execution budget. Do not simply delete the predicate or infer authority from matching task ID. |
| `manager.py:complete` / engine settle_completed | Ordinary completion rejects any retained deadline slot | A real blocker for tasks proving all goals BEFORE expiry: current final settlement requires expired DB clock. Implement separate evidence-backed early done settlement or explicitly retain unsupported behavior. Do not call expired finalization early, erase the lease, or mark done from model text. |

The grep inventory above covers explicit runtime rejection gates in the current
Python task path. Task chat, generic initialized handles and archive restore are
not alternative routes around those gates. Existing manual helper permissions do
not authorize natural timed admission.

## Minimum usable opt-in owner, not an automatic timer

Introduce one code-owned timed application owner paired with the existing ONE
TaskRuntimeApplication/manager/host/admission controller. It owns explicit timing,
observation/freeze and finalization plans, exact canonical transcripts, original
executor and policy revisions. Require cleanup and all final prerequisites at
construction; missing configuration rejects positive create before persistence.
Default application remains zero-timeout only.

After actual prepare, register each exact bound handle/kernel with this owner
before the first engine tick. Expose explicit operations for an already registered
generation: observe once, finalize once, cancel finalization, and bounded inspect/
drain. Do not select an arbitrary ambient task or reconstruct owner tokens from a
request. Observer readiness remains informational; finalization refreshes exact
ownership, DB expiry, clean drain and independent start grant.

No observer thread starts by default. A manual/operator-driven opt-in can be
useful only if its documentation states that timely expiry/finalization requires
an independent caller cadence. The synchronous engine pump cannot be the sole
caller while its provider is blocked. Relative provider clamps reduce individual
IO budgets but cannot force arbitrary callbacks to stop. A later explicitly
started observer loop needs separate tracked lifecycle review.

## Required schema/setup preflight

Base task/exploration schema plus explicit `artex_task_bootstrap`,
`artex_final_assessment` and `artex_final_settlement` journals must already exist
with compatible contracts. Their installers require exact expected DB/public
schema and explicit allow_create; never run them implicitly from HTTP create,
readiness or finalization. Add read-only compatibility checks at opted-in setup,
including timestamp/row-lock prerequisites, before declaring timed admission ready.
Check executor identity/transaction support and canonical transcript namespace;
no default directories, credentials, provider fallback or new tool grants.

Final restricted configuration has independent finite limits and model identity;
its read-only list_goals tool cannot establish a missing proof. Settlement supports
local confirmed proof and source done/timeout only. Inherited/goalless/unknown
proof shapes must be rejected at positive admission or handled as explicit
unsupported final outcomes; they cannot be silently completed.

## Lifecycle blockers to resolve before opening gates

1. **Early completion:** attached lease blocks normal `manager.complete`, while
   final journal insists database expiry. This is not fixed by observer wiring.
   A task that proves its goals promptly must not wait for its deadline simply
   because the port lacks a guarded early terminal receipt.
2. **Clock initialization/expiry inside dispatch:** expired budget before transport
   must become ordinary frozen/draining progression with canonical cleanup, not
   an uncaught engine exception that permanently quarantines otherwise known work.
   Test first dispatch after delayed configuration and actual positive SQL path.
3. **Pause:** source absolute deadline continues. Retained suspended owner can be
   observed/finalized, but resume transfer is unsupported. Reject resume without
   resetting its time. Before first dispatch, pause must not create a clock.
4. **Shutdown accounting:** current listener stops admissions, HTTP, pump, calls
   owner.cancel_owned, requires host all_settled, then closes resources/pool. Lease
   phases already retain host ownership, but an external manual finalization call
   is not itself tracked/joined by the application. Add explicit active finalizer/
   observer invocation inventory and stop-new-actions latch. Require that inventory
   drained AND host all_settled before storage/provider closure. A committed SQL
   terminal result with uncertain registry confirmation must still retain resources.
5. **Timeout parsing/config:** validate finite integer 1..2**31-1, consistency of
   provisional versus persisted timeout and digest, and forbid changing timeout
   after prepare. Do not rely on the later stamp to discover mismatch after task
   has already been inserted/admitted.

## Safe implementation order and ownership

A. Architecture/lifecycle owner: typed timed app owner + exact registration and
manual invocation inventory; no gate change. Separate persistence owner: source-
faithful early-done evidence/receipt protocol if approved. Independent reviewer
checks original ownership and callback/cancel/close races.

B. Assembly owner: timeout-bound durable configuration and read-only schema
readiness, exact owner forwarding through bootstrap setup/runner/handle. Keep
positive gates closed until dependencies and fixtures agree.

C. Engine owner: exact timeout/clock-aware SQL snapshot branch and normal budget-
expiry cleanup behavior. Gate opening is one isolated patch restricted to actual
fully configured timed mode. Defaults/initialized/recovery modes remain rejected.

D. QA: actual fresh PG, one authenticated app, natural HTTP timeout>0 without
private pin edits. Create→goals (NULL clock)→planner first call (one stamp)→distinct
worker (same lease)→independent observer while blocked→cleanup→DB-expired final
receipt→truth-based terminal retirement. Also early goal done, never-stamped pause,
paused expiry, denied grants/schema/config, missing proof, postcommit uncertainty,
concurrent final/cancel/shutdown and unchanged zero-timeout behavior. No real model
or network targets: fixed inert responses and canonical temporary transcripts.

## Beyond the minimal supported path

Production recovery still needs durable process-restart ownership/lease takeover,
resume and timeout revival, multiprocess coordination, distributed canonical-store
reconciliation, complete final proof tools, inherited/goalless behavior, automatic
cadence/reliable wakeups and hard containment for uncooperative tools. None should
be implied by a successful single-process natural admission fixture.
