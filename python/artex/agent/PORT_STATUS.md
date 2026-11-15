# Agent orchestration primitives: partial, offline only

Sources inspected: `agent/taskclock.go`, `runinfo.go`, `constraints.go`,
`deferred.go`, `cancelcause.go`, `terminalreason.go`, and goal/worker integration.

Implemented:
- Immutable task-clock/run-attribution metadata, explicitly passed between calls.
- Deadline/run-budget clamping with injected clock and the upstream one-second floor.
- English operation-constraint prompt block; unknown constraint kinds deny.
- Skill-unlock replay from normalized stored tool-use history (injected callback).
- Latest tool-call trace accounting, including stale completion protection.
- English terminal explanations and a named abort-cause data structure.

Safety differences: constraint loading errors propagate rather than silently omit
boundaries. Terminal hints do not assert that assets/facts were persisted without
an integrated SDK proving it. Non-finite clock/budget values are rejected. Prompt
constraints alone are not executable policy enforcement. An expired deadline's
one-second clamp is upstream-compatible budget calculation, NOT permission to
start work. History replay is for trusted stored messages only, not user text.

Not implemented: Norma agentcore/harness/tool/permission/transcript adapters,
provider calls, event streaming/capture, planner/worker/main/chat/retest loops,
compaction/wrap-up, tool catalog/assembly, goal decomposition, finding workflows,
DB writes, cancellation propagation/timers, execution guards, or deferred system
prompt rendering/caching. `RunContext` is metadata, not a cancellation runtime.
Terminal reason string mapping must be checked against the actual Norma adapter.
`RunTrace` requires serialized event dispatch; it tracks only the latest call,
matching upstream and not a general parallel-call ledger. No target execution.

Validation: nine offline unittest cases in `python/tests/test_agent_core.py`,
covering deadline vectors, invalid budgets, immutable attribution, denied unknown
constraints, fail-closed constraint loading, skill replay/filtering/error propagation,
stale trace completion and unknown terminal handling. No dependencies installed.
