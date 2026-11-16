# Retaining uncertain process effects

A reproduced standalone composition defect converted BashTool CleanupUncertain
into an ordinary tool_error, letting a directly composed worker settle despite
unconfirmed process cleanup. The narrow fix introduces runtime EffectUncertain;
CleanupUncertain retains its ProcessError type and also carries that marker.
BoundedAgent propagates it before generic error conversion instead of fabricating
an ordinary terminal RunResult. No worker behavior was otherwise broadened.

Unchanged inert regressions and an actual host/bootstrap/engine/worker fixture
verify pending canonical transcript, original running claim, retained cleanup
ownership, blocked drain and no replay. Independent targeted runtime/cleanup
review passes53 cases. No subprocess was launched by these fixtures.

Default TaskRuntimeAssembly still excludes Bash/non-graph catalog names. This
fix covers explicit component composition and a prerequisite for future safe
integration, not demonstrated deployed Bash reachability. No default shell,
process policy, background task, PTY, custom script, MCP discovery or new
execution authority is installed. Real process termination and output limits
remain separate adapter contracts.

## Frozen verification

The full checkpoint passes 2915 collected tests and 458 SQL syntax checks.
Exact PostgreSQL stage104 passes two component/application groups and three
unchanged focused regressions. The real owned execution retains its actual SQL
claim and canonical pending turn; a separate actual application rejects Bash.
No subprocess was executed. Fixture-only corrections and exact manifest are
recorded in ISOLATED_QA.md.
