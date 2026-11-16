# Dynamic task coverage reads

Optional dynamic GET composition reuses the actual `TaskCoverageStore` and
`TaskCoverageService` for:

- `/api/tasks/{id}/coverage`: `enabled`, `scope_rows`, `denominator`, `tested`,
  nullable fractional `pct`, and `by_type` counts.
- `/api/tasks/{id}/coverage-graph`: source-shaped `nodes` and `edges`, including
  concrete asset IDs where present and connecting root-domain/company nodes.
- `/api/tasks/{id}/asset-refs?asset_id=N`: `intents`, `facts`, `findings` buckets
  preserving source IDs and explicit direct-source provenance.

The graph tab calls `api.taskCoverageGraph(taskId)` then, on selection,
`api.taskAssetRefs(taskId, node.asset_id)` in
`web/src/app/(main)/function/tasks/detail/_tabs/coverage-graph-tab.tsx`.
The dynamic overlay now supports that sequence without fixed-task rebinding.

## Authority and bounds

The dynamic router authenticates before resolving the canonical path task ID.
It retains the actual task/exploration/read revision and rejects missing,
archived, deleted or remapped task contexts. There is no active/global fallback.
The builder requires three separate callbacks when coverage is enabled:
`coverage_authorize(scope, source_task_id, source_exploration_id)`,
`coverage_request_authorize(request, scope, CoverageSelection)`, and
`coverage_asset_authorize(scope, selection, asset_ids)`. The immutable selection
contains action and optional selected asset ID. Optional
`coverage_connector_authorize(scope, kind, value)` is denied when absent.

Source scope checks remain in the actual store. Asset-reference selection is
approved before asset SQL; graph disclosure is approved against the concrete
returned asset IDs after projection. Final source/connector/asset/request grants
run after the existing service's serialization and guard exits. The router also
retains its original authentication callback through the final response boundary.
Source and connector grants are rechecked after the final asset-policy callback,
which can itself revoke prior authority. This is a defined final boundary, not a
fixed-point guarantee for arbitrarily mutually mutating host policies.
No asset visibility grants execution, scanning or testing permission.

SQL row/byte bounds are inherited from the reviewed actual store, with at most
500 configured result rows and a 1 MiB response budget in this builder. Overflow
fails closed rather than silently expanding scope. Direct source tasks require
independent grants; recursive inheritance and archives are not synthesized.
Connecting nodes require their existing explicit connector policy. The wrapper
does not invent global asset lookup authority.

## Interpretation and evidence

`tested` and coverage totals retain the source's approximate persisted graph
semantics. They do not prove complete testing, vulnerability absence, live target
reachability or task completion. Disabled coverage keeps the source disabled
aggregate shape; graph/reference availability is a separate source behavior, not
a covert request to enable execution. No assets or graph state are modified.

Six new owner synthetic tests pass: two tasks in one authenticated application with
graph-to-reference requests, concrete scoped IDs, late graph asset denial after
actual serialization, selected-asset denial before SQL, malformed selectors and
no inferred connector grant, and company-connector revocation from the final
asset callback. Three independent regressions additionally test source revocation
during serialization/from the asset callback and exact disclosed asset IDs. The existing builder/router/goals/main-session tests
were also run. Independent review and actual PostgreSQL acceptance are pending
separate gates at authoring; no frontend, browser or live-target test is claimed.

Strict inventory metadata adds only the three exact audited alternate declaration
groups. Generated reports are intentionally left for the next frozen checkpoint;
registration totals do not establish deployed route or behavioral parity.
