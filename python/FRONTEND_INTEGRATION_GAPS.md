# English frontend → Python integration gaps

Source-only review of the current working tree, 2026-11-15. No Next/npm build,
browser, listener, live provider or database was run. This is a workflow map, not
a production-readiness claim or another route-count report. Concurrent work may
change the named modules; recheck before integrating a slice.

## Deployment baseline applies to every screen

`web/src/lib/api.ts:115` sends JSON requests to same-origin `/api`, with a bearer
token. `web/next.config.mjs` development rewrite defaults to localhost:8787 and
can use AUTOPENTEST_API; static export requires an external reverse proxy/static
host. SSE chooses NEXT_PUBLIC_SSE_BASE, otherwise development browser hostname:8787
(`api.ts:sseUrl`). These destinations must actually point to a Python WSGI host.
An existing dev-only CLI, `artex/__main__.py`, accepts explicit `--serve` and binds
stdlib WSGI to `127.0.0.1` using externally supplied DSN/signing key and bcrypt.
It mounts persisted metadata/CRUD contracts, not the provider/task runtime, pump
or frontend. `test_cli.py` mocks the socket boundary; it does not prove a real
listener acceptance test. The remaining gap is checked full-runtime resource and
lifecycle composition into that transport, not the absence of any listener code.

Neither `build_application` nor `build_persisted_application` opens a listener,
serves the frontend, sets TLS/timeouts or creates credentials/connections.

`server/application.py` is explicit dependency injection. Bare build_application
registers many routes that return 503 because stores/runtime callbacks are absent.
The persisted factory constructs repositories and task metrics, not a complete
agent runtime. Fresh schema bootstrap creates schema, not builtin agent catalogs,
model configuration, capture subsystems or a runnable installation.

### Resolved cross-screen defect: query-token SSE compatibility

The frontend always appends `token=<JWT>` in sseUrl. AuthService.extract_token
accepts this, but LogService.stream previously permitted only `since` with
max_num_fields=1, and SideQuestionHTTPService._handle permitted no query on events. Before the fix, valid frontend
EventSource URLs authenticated and then returned 400. References:
`web/src/lib/api.ts:sseUrl`; `web/src/hooks/use-side-questions.ts:131`;
`web/src/app/(main)/system/logs/page.tsx`; `server/auth.py:extract_token`,
`server/logs.py:stream`, `server/sidequestions.py:_handle`.

Resolved: both services now allow one reserved token query field bounded to
16 KiB while keeping duplicate/unknown-selector rejection and Router auth first.
Independently verified all four test_frontend_sse_auth.py WSGI tests using actual
query-JWT URLs, including since+token, side events, unauthorized-before-callbacks,
unknown/duplicate rejection and no token echo. This removes the protocol blocker,
not missing runtime adapters or deployment requirements.

## 1. Login and initial setup

**UI:** `(auth)/login/page.tsx`, `(auth)/setup/page.tsx`, api.ts authStatus/login/
initPassword/changePassword. Required paths: GET /api/auth/status; POST /api/auth/init,
/api/auth/login, /api/auth/change-password. These exist in `server/auth.py`.

**Potentially usable with real adapters:** password initialization/login and token
validation have byte HTTP/WSGI contracts. Requires schema/settings repository,
production password codec and durable >=32-byte signing key supplied by host.
No default key, database connection or deployment exists. A successful mock login
is not evidence that the English application can be served. Keep the same-origin
cookie/bearer behavior and ensure unauthorized requests do not call runtime stores.

## 2. Configure and activate a model

**UI:** `(main)/system/llm/page.tsx:372–426,803–845`; api.ts LLM methods.

- GET /api/llm/profiles can read persisted public DTOs with ProfileStore.
- POST /api/llm/profiles and /api/llm/profiles/active are registered but need an
  explicit ProfileCoordinator. The base persisted factory does not construct it;
  `build_profile_runtime_application` now constructs and wires it explicitly.
- GET /api/llm requires an injected runtime snapshot. Stored active profile alone
  is deliberately not accepted as proof that a runtime provider is configured.
- POST /api/llm/test and /api/llm/models, DELETE profile, and legacy POST /api/llm
  are registered **unsupported 503**, not completed configuration capabilities.
- GET /api/llm/pool, POST /api/llm/pool/reset and retry-policy methods are unregistered.
  UI also uses /api/settings, which has no service registration in application.py.

`providers/factory.py` supplies construction primitives; `server/runtime_setup.py`
now supplies opt-in persisted profile/coordinator/runtime-snapshot wiring and the
correct ProfileApplyPending error adapter. It performs startup profile/task reads,
not inference. `server/chat_setup.py:build_chat_application` composes that helper
with authenticated conversation routes. Neither changes default CLI/deployment
wiring. Transport origin/TLS settings must be explicit. It supports OpenAI and
Anthropic subsets, not the UI's openai-responses choice, proxy/session headers,
rate limits or general retry settings. In particular UI zero/inherited retry
settings do not mean supported automatic retry: the factory requires explicit
no-retry settings. Unsupported choices must be disabled/explained or implemented,
not silently discarded. ProfileApplyPending must be configured for correct
persisted-but-not-runtime-applied HTTP feedback. Key hints/redacted URLs are
presentation-only and cannot be roundtripped as replacement credentials.

## 3. Open chat and send a message

**UI:** `(main)/chat/page.tsx:394–413,518–754,1087`; api.ts:1006–1037.

The screen first needs GET /api/agents and /api/conversations, POST conversations,
then GET/POST /api/conversations/{id}/messages, message detail/history and stop.
Progress: conversation list/create, rename/pin PATCH and profile PATCH are
registered through ConversationService. They require explicit agent-existence,
selected-profile availability and live running-state callbacks. Nonempty lists
without live running authority return 503, not invented idle. DELETE and batch
delete remain registered unsupported 503 pending coordinated cleanup.

`server/chat.py` now supplies POST messages (source-shaped nonstreaming 202),
GET message history/detail and POST stop. `chat_dispatch.py` owns bounded local
admission and cancellation; `db/conversation_activity.py` projects user and terminal
activity with frontend seq/ts DTOs. Historical reads and stop authorize without
requiring a currently available provider. This is polling, not incremental token
or tool streaming. Client IDs fence duplicate execution, but SQL activity and JSONL
remain separate commits: crash recovery and cross-process authority are not solved.

`server/chat_setup.py:build_chat_application` now explicitly mounts metadata,
profile configuration and conversation execution on one JWT-authenticated WSGI
application. Agent support, authorization, grants, tools, prompt resolver, profile
transport, transcript root and single-process ownership must all be supplied.
This conversation assembly is not wired into the packaged listener. The existing
`python -m artex --serve` loopback CLI mounts the persisted metadata/CRUD application,
not conversation execution. Optional catalog_store and explicit
catalog prompt/readiness adapters now mount GET/POST /api/agents and metadata
routes. Attachments and mentions remain unavailable, so this assembly alone does
not make the complete chat screen usable. Independent mocked JWT assembly tests exercise configure/send/poll
and cancellation/drain, not browser or live-provider QA.

`agent/chat.py`, `transcripts.py` and `chat_transcripts.py` now form a real durable
conversation bridge used by this optional service, not just disconnected storage
primitives. No default shell/web/MCP tools are inherited. Persisted-but-uncertain
turns retain ownership instead of claiming clean completion.

Main-task POST /api/chat and task status/stop have a separate TaskChatService,
TaskChatDispatcher and scoped transactional activity sink. They are unregistered
(404) until explicitly composed with host lifecycle/revision/authorization adapters;
the conversation service does not own a conflicting task placeholder route.
Main history/segments, mentions and uploads remain unfinished. The bounded task
turn does not imply a complete autonomous planner/worker loop or default AgentHost
adapter. No task-main readiness follows from the conversation assembly.

Side-question routes ARE registered (three parent families plus events/cancel),
but all parent/request authorization, model availability, preparation, coordinator,
dispatcher, cancel/clear and wait adapters are explicit. No main capture means no
admissible side question. The query-token SSE issue above is now fixed; missing runtime adapters remain.

## 4. Create, run and inspect a task

**UI:** `(main)/function/tasks/page.tsx:3121–3145` create/upload; api.ts:244–368
(task DTOs/options/control); task-detail API methods around api.ts:688–780.

**Persisted metadata slice:** task list/detail, name/pin PATCH, categories,
templates and pause/resume/batch routes exist. Persisted factory creates manager,
repositories and metrics; no runner means create/resume is unavailable. Missing
DTO metrics is not fabricated as zeros. Templates save rules; they do not execute
them. Company scope edits and cleanup remain unavailable.

**Execution blockers:** `agent/runner.py` executes one explicit bounded conversation,
not the source main/planner/worker graph loop. It requires bootstrap/preflight,
configured model/gate/tools and synchronous result sink; seed_first_intent rejects.
Host/scheduler require explicit lifecycle ownership, polling and settlement.
Graph goals/proof/intent generation and approved task tool catalog are not supplied
by the HTTP factory. Profile availability validation must precede persistence.
The UI sends coverage=true, profile/source/company/rule options and may attach
uploads: these must be supported or rejected before task creation, not ignored.

**Detail/management blockers:** task profile-chain PUT /api/tasks/{id}/llm,
worker control/message routes, main chat/history/segments, activity/status streams,
task delete/archive/restore, token/traffic/report/workspace flows remain incomplete
or absent. /api/stats exists only with explicit runtime snapshot; global assets
are intentionally administrator counts. /api/exploration read/nodeboard routes
require authorized live task-store resolvers and asset-label adapters; these are
not inferred from task manager. A task list rendering does not prove the detail
screen or execution loop is functional.

## 5. Inspect findings

**UI:** `(main)/function/findings/page.tsx:177,324–411,609–738`; api.ts finding APIs.

GET /api/exploration/findings and /{id}, plus selected metadata PATCH, have real
store contracts; persisted factory uses the documented global administrator
FindingStore. Anchored assets need an explicit resolver. No automatic global
asset fallback is granted. Stored report read is not report generation.

The screen additionally requests groups and asset-tree, active retests, export,
status updates, deletion and deepen. Registered stats/groups/asset-tree/export,
lineage/retests/deepen/delete paths return unsupported 503. Status changes requiring
notifications/provenance also fail closed; traffic binding/body routes and overall
report generation are not supplied. Thus flat unanchored finding data can be
read with adapters while grouped browsing and many visible actions still fail.
Do not label the whole finding screen usable based on GET-list unit tests.

## Recommended next three coherent vertical slices

1. **Authenticated deployment + supported model configuration.** Explicit server
   entrypoint/config, fresh-schema/seed boundary, stable signing key/password codec,
   reverse proxy and SSE token compatibility. Deploy the existing opt-in ProfileFactory/Coordinator and
   runtime-snapshot assembly for a clearly limited provider configuration; make unsupported
   controls explicit. Acceptance: real English setup/login, save+activate supported
   profile, redacted reload and an authorized inert/local test adapter, without
   network probes or hidden runtime startup by default.
2. **One durable conversation end to end.** Deploy the new optional conversation assembly plus one approved
   chat agent and agent catalog route, validate messages/history/status/stop and
   idempotent retries and no false persisted/completed results after failed commit. Wire main
   capture before side questions; defer upload/mentions until context is actually
   preserved. Acceptance: UI sends a harmless message, observes durable response,
   reloads history, cancels and handles duplicate/failure without rerunning tools.
3. **One authorized task-to-finding workflow.** Explicit supported creation options,
   graph bootstrap/planner/worker lifecycle and gate, scheduler/settlement, task
   progress/activity DTOs and finding provenance. Add flat finding/detail/report
   read and required asset resolver before grouped/export/retest extras. Acceptance:
   an inert fixture task advances documented states and displays a durable finding
   with evidence; pause/drain and stale generations are verified. Never substitute
   a generic chat response for task completion or a fake finding.

For each slice, contract fixtures should use exact frontend method/path/query/body
shapes, then approved browser QA can verify the actual screen. Until that execution
is authorized, source inspection and mock tests are the evidence ceiling.
