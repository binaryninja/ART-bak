# Explicit tuning-backed development assembly

`prepare_development_runtime` connects the existing strict runtime-tuning JSON
to real task runtime/read/listener/session components. The host must first acquire
the explicit RuntimeLease, then construct its TaskRuntimeAssembly, shared pool,
RuntimeResourceOwner, signing key/password codec and independent authority
callbacks. This helper does not invent those policies or discover credentials.

The existing inert config checker remains unchanged. Tuning bytes go through its
strict parser; JSON can set only listener, pump and read bounds. Those values
configure DevListenerConfig, EnginePump and the real dynamic TaskReadBuilder.
Code-owned read-policy kwargs cannot override executor or configured row/byte
bounds. Selector/current/guard callbacks and policy revision remain explicit,
independent inputs. No optional dashboard, skill/archive catalog, manual/timed
execution capability, global permission or fallback policy is enabled.

Before any builder/application construction, the helper claims the acquired lease
for a retained DevelopmentAssembly. This rejects reused/competing claims before
startup SQL. On success RuntimeSession atomically receives that exact claim;
there is no unclaimed lease interval between assembly and session. Existing
single-process/capacity declarations remain mandatory and are not inferred from
the presence of a lock file. Assembly construction may load task inventory but
starts no listener, pump, provider call or background thread.

On an error after the claim, DevelopmentAssemblyFailed retains the original
runtime/owner/lease plus each completed intermediate component. Its generic text
and default assembly repr do not print policies, resources or secret values.
The assembly retains the lease claim. The host must explicitly reconcile partial
construction and close resources before `lease.release(owner=error.assembly)`;
that release call is NOT itself cleanup. There is no automatic rollback, database
state restoration or retry, and exceptions during lower-level constructors may
leave partial internal state not represented by a completed outer component.
Errors before a claim leave the original caller-owned lease untouched.

The successful result exposes `.session.run_until_stopped(stop_event)` for the
previously verified foreground lifecycle. This is a Python host-assembly API,
not a standalone user CLI. Actual provider configuration, transport origins,
durable transcript roots, lifecycle/target grants, cancellation and resource
closure still come from the explicitly authorized host. No safe default for those
is implied, and arbitrary supplied callbacks are not contained.

Six authored tests pass with inert stores. They verify exact config propagation
through the real read builder into scoped activity bounds and read response caps,
actual application pump values and listener configuration, no startup effects,
validation before SQL, claimed-lease refusal before construction, exact-owner
claim transfer, retained partial application on failure and missing policy refusal.
Existing session/foreground and config-check regressions are rerun separately.
No real database, provider or target is used in these new assembly tests.
Independent review/full frozen acceptance remain pending. No merge, deployment or
production readiness is claimed; held dashboard/private work remains excluded.
