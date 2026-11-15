# Explicit goal, policy and scope components

GoalPlanner now persists structured goals with root lineage through actual scoped
stores and durable transcript receipts. Optional ConstraintTools can accompany it
with the English source goals prompt; exact normalized allow and deny proposals
require independent host approval. Model extraction is not operator permission.
Asset scope metadata and its optional native tool likewise require exact approved
entries; hostnames are not silently promoted to root domains. Partial tool batches
preserve their known earlier commits and do not imply whole-turn atomicity.

These components do not by themselves implement task creation/bootstrap. Durable
generation allocation and orchestration are separate work. No fallback turns
ordinary model prose into goals, proof, target authorization or task completion.

Optional HTTP services implement source-shaped task constraint, goal, local scope
and profile configuration paths with exact task/executor/revision ownership and
host-confirmed idle mutation policies. Source live edit/revival/notification
semantics are not inferred. Goal text/class edits preserve state and evidence;
state injection is rejected. Deletes need separate explicit authorization.
Inherited scope GET currently fails unavailable rather than silently omitting
source tasks. Profile changes use ordered chains and revision CAS; post-commit
coordination uncertainty requires reconciliation, not an automatic retry.

Bounded list and goal response queries enforce SQL-side row/byte sentinels before
payload transfer. Oversized existing data fails rather than yielding an incomplete
policy/goal view. No default routes, credentials, models or target tests are enabled.

An explicit EnginePump can advance already-owned handles and poll settlement.
Construction starts nothing, stop does not implicitly cancel tasks, and a timed-out
thread remains owned. Planner cancellation can finalize only its own receipt with
separate explicit same-generation persistence authority; missing/stale authority
still quarantines unresolved work. This never grants model/tool execution after
cancellation. Pure deadline and wrap-up policies are ported, but task deadlines and
extra settlement execution remain unconnected to the engine.

MCP metadata CRUD is separately opt-in and never starts a client or process.
Public credential-bearing configuration and cached tool text remain opaque;
redacted placeholders cannot be saved. The native MCP bridge wraps an explicitly
provided client and remote tool definitions with fresh per-call grants and pinned
session/scope. It performs no discovery or automatic registration. Unsupported
schemas/content and uncertain effects stop without retry; live transport/TLS and
legacy SDK behavior are not proven by inert RPC tests.

Workspace list/read/download/write handlers use explicit workspace root identity,
per-action policies, finite limits and existing no-follow filesystem primitives.
There is no inferred root or default mounting. Writes reuse atomic replacement
and report uncertainty honestly. Exclusive host control of root/mount/writers is
still required; this is not an operating-system sandbox against hostile peers.

Frozen checkpoint: 1,779 authored tests pass; 364 captured SQL statements parse.
Static method/path coverage is 137/261 (124 unregistered), including optional and
unavailable contracts, not a completed-feature count. Fresh bootstrap journal and
creation-to-engine ownership integration remain separate work, not implied here.
