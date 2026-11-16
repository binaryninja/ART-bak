# Gate-side effect uncertainty propagation

`BoundedAgent._authorize` now rethrows the original `EffectUncertain` instance
before the existing RunStopped/generic Exception handling. A permission gate may
have attempted a persistent audit/admission operation before its commit becomes
unknown. Converting that marker to `gate_error` previously produced an ordinary
terminal result and could release the owning work claim despite ambiguity.

This is the same explicit marker already propagated from tool callbacks. Ordinary
gate exceptions still become gate_error; RunStopped reasons remain unchanged.
No default gate, provider, durable-context integration, authorization expansion or
new action is installed. It does not prove any gate has side effects, reconcile
unknown effects, or give permission to retry. The gate owner is responsible for
retaining its uncertain state. Provider-transport semantics are unchanged.

Three authored inert tests preserve two pre-fix failures and demonstrate same
exception identity, no transport before uncertain model grant, no tool execution
after uncertain tool grant, and legacy exception behavior. An independent actual-owned engine/worker regression also passes: no provider
call, pending canonical transcript, retained running claim and cleanup ownership,
blocked drain and no replay. No real model,
process, tool or user database operations were performed by these tests.
