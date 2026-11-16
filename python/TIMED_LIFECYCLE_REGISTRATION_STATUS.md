# Explicit post-prepare registration composition (review candidate)

New `TimedLifecyclePolicy` and `PreparedTimedLifecycle` compose existing actions
on the existing TaskRuntimeApplication/host/manual registry. Setup requires exact
policy type, the SAME TaskTimingPolicy instance already pinned by the actual
runtime, cleanup capability and explicit track_manual_actions before inventory
reads. No second manager, implicit schema install, provider, clock stamp or action
tick is created. Default applications are unchanged.

The registration seam requires the original same-thread host owned-work slot,
then an actual idle installed prepared BootstrapHandle with its original bound
PreparedTaskTiming/store/kernel. Exact objects and callbacks are pinned; one-time
registration retains all constructed products on failure. Subsequent runner
advancement/retirement must be fenced by the integrating runner seam. The binder
does not fabricate an active frame or infer a clock from a task ID.

The policy explicitly supplies scoped frontier permit issuance and authorization,
observation/freeze grants, and optionally an exact FinalizationPlan. Missing final
plan reports finalization_configured=False. admission_ready is always False in
this prerequisite stage. An explicitly supplied plan remains host authority; it
is not a replacement for the missing source-backed persisted final-profile
adapter, schema readiness checks or positive admission configuration. Goals are
not wrapped/stamped and no gate changes here.

Nine authored tests now pass, including actual original JWT application → goals →
BootstrapHandle → same-thread host-owned post-prepare registration. Positive
prepared timeout pins are explicitly synthetic; no active flags are fabricated.
The fixture confirms same-host registry/original timing resources, no first clock
stamp or extra model call, sticky partial registration under explicit capacity=1,
invalid scoped permit refusal, and failure of the next actual host-owned step.
Missing final_plan blocks the next positive execution step with no provider call;
status.admission_ready remains False even when a plan is supplied.

The runner seam now exists: install once before admission, after actual prepare
returns register under the original owns_work slot, retain a separate attempt
ledger for even pre-entry failures, and block advance/retirement on incomplete or
failed registration. It does not open any positive admission/SQL gate. Existing
host/registry drain retains pending resources; no pool-close success is inferred
from pump stop or a completed goals call.

Twenty focused owner/review tests pass with SQL doubles. PostgreSQL stage 113
full persisted-role positive registration is blocked by the original configuration
digest when synthetic timeout pins change after genuine zero bootstrap. One
actual negative group confirms retained owner/no stamp/no additional model call
and next-step refusal. No guard was weakened. Listener/pool closure integration
remains unverified; see ISOLATED_QA.md. It is not natural positive HTTP execution, production recovery or
source-complete final profile construction. Explicit supplied FinalizationPlan
callbacks remain host authority; the persisted final-profile adapter is still a
separate required feature rather than an inferred fallback.
