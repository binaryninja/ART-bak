# Read-disclosure authorization review

A targeted regression review found that several optional readers checked current
scope/resource identity after a fetch but did not recheck the separate read grant.
Those are different conditions. If a host callback revoked permission while the
query or projection completed, already-fetched content could still be returned.

The bounded remediation tracks original selectors and checks the applicable read,
raw-data and request grants again before disclosure. It also retains original per-read policy callbacks and refuses replacement during
the snapshot, so swapping in a permissive callback cannot conceal revocation.
It preserves resource/current fences, does not replay mutation permissions, and does not broaden SQL scope.
Affected reviewed surfaces include command history, token accounting, persisted
intercept inspection, workspace reads, pool diagnostics, task-scope lists,
intercept-rule lists, LLM-record metadata/body projections, and coverage source/
connector reads. Concrete regressions and scoped acceptance evidence accompany
these changes. This list is not a repository-wide security certification.

The host must serialize cooperative scope/permission changes using the supplied
lifecycle guard. Callback checks are not a sandbox against malicious in-process
mutation or uncooperative writers. Separate adapters remain explicit opt-in;
passing read checks grants no permission to execute tools or contact targets.

During integration, paused lifecycle work is excluded from the disclosure-fix
checkpoint. Its manager/host/clock handshake is incomplete and positive task
timeouts remain unavailable. Test counts in README describe a frozen checkpoint,
not arbitrary concurrently edited working files.

Direct TaskScopeStore.list_bounded now requires an explicit read_authorize callback;
missing authority refuses before SQL. Existing insertion approval is not reused as
read permission. Hosts using this optional direct API must supply the new grant.
