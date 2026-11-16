# Bounded task progress and fixed-task overview

Optional activity projection derives one terminal result and real usage record per
confirmed planner/worker turn from the canonical scoped transcript receipt. It is
not incremental model-token or tool-event capture. Goals initialization is not
projected by this adapter. A task-row lock serializes receipt lookup and activity
insert; duplicate reconciliation compares both the receipt and actual persisted
payload/usage. Caller identity is copied before policy callbacks. JSONL and SQL
are not one transaction: uncertainty quarantines execution rather than rerunning
inference. A receipt marker is bookkeeping, not execution or completion authority.

Activity list/detail/history and finite SQL-backed SSE replay are explicit opt-in.
They preserve source cursor/session DTOs, nested JSON metadata (hiding only private
projection bookkeeping), and exact task/raw-data grants. Every SSE batch retains
its own selector; original-request and current-batch grants are checked before SQL
and buffered delivery. No SQL transaction or host guard spans a yielded frame.
EOF means reconnect, not task completion; this is not a lossy broadcaster or an
unbounded server loop. Inherited/archived sessions remain unavailable.

The read-composition helper exposes an explicitly fixed task's metadata, stats,
intents and findings, plus separately authorized global summaries/public profile
metadata. It reuses the authenticated app and introduces no mutation routes. The original
JWT authority is checked both before the read and after JSON serialization;
callback replacement discards the prepared response.
Task finding reads use graph nodes, standalone triage metadata, direct-source
provenance and separately authorized asset labels, not a global finding alias.
Oversized, ambiguous, archived or unauthorized data fails closed.

This is a fixed-scope view: other/new tasks require an explicitly authorized new
binding. It is not automatic all-task frontend composition. Optional activity
factory wiring is likewise fixed-scope and absent by default. Frontend request
sequences are tested through WSGI without running the original frontend build or
browser. Full dashboard/live-session parity, dynamic read resolution and packaged
server deployment remain separate work. See ISOLATED_QA.md for bounded evidence.
