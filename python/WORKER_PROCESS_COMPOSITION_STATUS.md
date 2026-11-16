# Explicit worker foreground-process composition

WorkerProcessPolicy requires an explicit runner, canonical existing working
root, absolute shell, immutable environment, policy revision and launch grant.
There is no default runner, inherited environment, shell permission or process.
Only an explicitly configured worker Bash catalog entry is added to the normal
local graph handlers; goals and planner stay unchanged. Cleanup policy is required.
Configuration identity includes settings while runner/grant identities remain
pinned in memory. Environment values are hashed locally, not logged as settings.

PreparedWorkerProcess binds once to the original prepared manager, outer handle,
engine, worker, token, transcript receipt and cleanup capability. An independent
launch ledger is reserved before runner entry. Active work prevents drain; an
exception, malformed result or changed binding after entry leaves sticky effect
uncertainty. Canonical transcript settlement cannot clear that ledger. No retry
or reconciliation shortcut is provided.

Two independently reproduced last-boundary defects were fixed before integration:
a lifecycle callback revoking the launch grant, and a final grant cancelling the
manager token. Final dispatch now refreshes the exact launch grant after callback
fences, followed by context/binding checks and the original manager token and
callback-free ownership check before ledger reservation and runner entry.

A valid return relies on the explicitly supplied runner's synchronous-drain
contract. It is not proof of OS containment, detached-descendant cleanup or hard
preemption. The runner must cooperate with cancellation and bound blocking work.
No background jobs, PTY, stored scripts/templates, MCP discovery, real commands
or default Bash enablement are added by the tests. Default application remains
graph-only unless this policy and catalog are explicitly supplied.

Nineteen owner/independent focused tests pass with inert providers and runners.
Actual PostgreSQL acceptance is recorded separately when complete; this document
does not claim real process cleanup validation or full tool parity.
