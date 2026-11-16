# Manual owned expiry observation (working implementation)

`agent/expiry_observer.py` now implements explicit `OwnedExpiryObserver.tick()`.
Construction is inert. No background thread, final model, journal allocation,
terminal settlement or positive-timeout runtime admission is enabled.

The observer retains the exact host, shared generation clock, original store,
manager/token/BootstrapHandle and installed lease. Kernel observation does not
stamp or adopt an existing clock. The separate read-only store operation verifies
actual task clock, prepared journal identity and database expiry, with original
guard and final grant checks. Frozen reads require the exact freeze capability.
The ordinary lease-read API has not been widened into mutation authority.

Each tick can call the existing host freeze with timeout zero independently of
the pump. Local conservative monotonic expiry can freeze early: clean drain then
returns `waiting_database`, not quarantine or finalization. Only clean drain plus
DB expiry returns `ready`, which is an observation, not a final execution grant.
Pending work returns `draining`. Suspended original owners retain their absolute
clock; no resume transfer, reset or restart adoption exists.

Eight owner tests use actual manager/host/bootstrap components with explicitly
synthetic positive prepared pins and inert SQL. A controlled actual planner blocks
in a separate host-owned engine advance while an independent tick freezes it;
release permits canonical cleanup and clean drain with exactly one model call.
Two independent final-callback regressions also pass: quarantined ownership and
revoked freeze grant cannot return readiness. Independent narrow review cleared 49 combined tests. Exact frozen-source
real PostgreSQL acceptance is recorded separately in ISOLATED_QA.md. Positive production configuration
remains rejected. Policy/SQL callbacks are cooperative; zero handle wait does not
imply a hard timeout for arbitrary callbacks or database calls.

The final-action and tracked-background lifecycle design remains planned in
`OWNED_EXPIRY_OBSERVER_PLAN.md`; no `finalize_once` method has been implemented.

The frozen checkpoint passes 2758 collected tests and 455 SQL syntax checks.
Exact-source stage93 passed three real PostgreSQL component groups and one
intentional positive-entry refusal. Real SQL confirms early local freeze followed
by ordinary database waiting and later readiness, plus suspended-owner handling
and callback revocation refusal. The blocked-provider threaded case remains
SQL-double protocol evidence, not naturally reachable positive timed execution
with the real engine.
