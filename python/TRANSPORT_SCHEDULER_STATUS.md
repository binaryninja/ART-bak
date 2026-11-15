# Provider transport, queued admission and company persistence

This stage adds opt-in functionality, not automatic production activation.

- The stdlib provider HTTP transport requires an explicit origin allowlist,
  verifies TLS, refuses redirects and implicit environment proxies, and requires
  explicit literal-loopback opt-in for plain HTTP. Request/response byte limits,
  cancellation checks and cleanup are tested with fake connections. DNS and
  blocking system calls are not forcibly interrupted; response iterators must be
  closed if abandoned. No real provider requests or credential discovery occur.
- Tick-driven FIFO scheduling connects public queued admission through TaskManager
  and AgentHost. Its adapter explicitly requires single-process ownership. All
  admissions must use the same coordinator for its capacity guarantee. Exact local
  lifecycle snapshots and generation checks protect against stale admission;
  distributed leases and final-planner deadline settlement remain unavailable.
- Independent tests found and fixed unavailable head-of-queue starvation and
  capacity loss after deferred admissions retaining live handles. Reentrant drain
  also fails without deadlocking. Cancellation still requires cooperative drain.
- Company metadata persistence provides scoped reads/search, metadata upsert and
  empty-scope creation, with exact domain/IP/ICP resolution precedence and bound
  transaction adapter. Scope edits/delete remain unavailable pending full global
  attribution recomputation and cleanup. No registry or network lookup is made.
- Three remaining bundled skill documents are translated to English without
  executing their commands. Chinese matching expressions retain their exact
  meaning through explicit Unicode escapes. Historical source policy conflicts
  are retained rather than silently expanded.

The full planner/worker/tool runtime and much of the original API are still absent.
See migration ledger, route inventory and independent review notes for limits.
