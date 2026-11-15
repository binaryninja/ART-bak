# Explicit persisted-role task runtime assembly

This optional composition replaces consumer-written bootstrap/planner/worker
lifecycle factories with authored components. It uses actual persisted task,
agent-role and profile stores; canonical scoped transcripts; bootstrap journal;
GoalPlanner, PlannerCycle and IntentWorker; and explicitly supplied local graph
tool catalogs. One authenticated application owns one manager, host, admission
controller and inert EnginePump. Construction does not start a listener, thread,
provider call or task tick.

The roles goals, planner and worker remain distinct. Provider selection follows
valid role binding, then the explicit active task chain, then an explicitly allowed
stored global profile. An exhausted/invalid task chain never silently falls back.
Profile read authorization precedes credential fetch. Host-local HMAC model IDs
avoid putting private profile configuration in public identities; they are not
cross-restart ownership receipts. No environment discovery, automatic quota
failover, profile reload or provider credentials are supplied by this assembly.

Configuration identity covers all three role models, prompts, graph catalogs,
limits and policy revision. Original callbacks, transcript namespace and resources
are retained, and installed execution/current authority remains separate from
read-only configuration preflight. Mutation of profiles, transcript storage or
host policy cannot be treated as permission to continue an old prepared run.

All host policies, prompts, transcript storage and graph catalogs are explicit
inputs. Enabled catalog/profile rows are not execution permission. Only bounded
native graph tools are composed here: no process, network or MCP tool execution
is silently enabled. Manual ticks or an explicitly started pump drive work.

Positive-timeout execution, initialized-task recovery/adoption, inherited task
context, live progress/frontend parity and production transport behavior remain
outside this slice. Verified completion-path integration evidence uses fresh
synthetic PostgreSQL data and injected fixed HTTP responses, not a real model or
external target. See ISOLATED_QA.md for immutable acceptance scope.
