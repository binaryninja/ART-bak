# Explicit conversation runtime checkpoint

The Python port now has source-shaped conversation metadata, asynchronous message
admission, history/detail polling and stop handling. This is a deliberately
configured runtime, not a default production installation or complete agent UI.

## Connected path

- Authenticated conversation POST validates configuration before durable user
  activity, registers cancellation before returning 202, and uses a bounded
  single-process dispatcher. History uses PostgreSQL conversation activities with
  the frontend's `seq`/`ts` DTO, not an invented JSONL response or SSE endpoint.
- The permission-gated ChatAgent runs an explicitly supplied provider/tool set.
  Transcript admission and terminal writes use the real JSONL adapter with
  generation checks, complete tool pairs and verified finalized-result reuse.
- Terminal activity is projected only after confirmed transcript persistence.
  History and stop authorization do not depend on a currently usable model.
- Profile runtime assembly requires approved transports, authorization, permission
  gate, agent prompts, an existing transcript root and explicit tools. Full private
  profile identity is rechecked at provider boundaries without exposing secrets.
- Conversation metadata and activity persistence preserve profile locking,
  pagination and nullable usage. Batched activity/title/touch updates use one SQL
  transaction. The explicit connection pool pins transactions to one executor,
  bounds capacity, retires uncertain connections and reports incomplete close.
- Filesystem tools are opt-in and root-bounded. They reject links/special files,
  use atomic replacement, preserve ordinary mode bits and refuse unsupported
  ownership or extended metadata rather than silently corrupting it.

## Important limits

SQL user activity, JSONL admission/finalization and SQL terminal activity are
separate durable steps, not an atomic distributed transaction. Ambiguous failures
retain unresolved ownership for reconciliation; neither crash recovery nor
automatic retries are safe to infer. Receipts are process-local and bounded;
cross-restart activity deduplication and distributed leases are not implemented.

Execution currently uses nonstreaming models and user/terminal activity rather
than incremental tool events. Attachments, mentions, retest settlement, coordinated
deletion, the complete agent catalog and full main/planner/worker orchestration
remain incomplete. Task chat is a distinct lifecycle from conversation chat.
Host TLS, concurrent serving, timeouts, durable credentials, migrations/seeds and
resource shutdown remain explicit responsibilities. Unit and inert-provider
integration tests do not prove browser compatibility or production readiness.

Independent review corrected cancellation swallowing, stranded dispatcher
futures, frontend activity DTO names, offline-history authorization and missing-row
driver behavior. See ISOLATED_QA.md for real PostgreSQL evidence and
REVIEW_FINDING_RUNNER_STAGE.md for scoped review findings.

Frozen checkpoint verification: 1,080 authored tests pass; 242 captured SQL
statements parse. The source inventory matches 82/261 Go method/path registrations
(179 unregistered), including optional services and explicit unavailable routes.
Registration is not feature parity.
