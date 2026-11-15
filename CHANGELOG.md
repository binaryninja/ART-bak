# Changelog

Notable changes to this project are recorded here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.15] - 2026-10-07

### Interception

#### Added

- **Built-in "deletion endpoint paths" interception rule**: the existing destructive HTTP rule recognized only the DELETE **method**, but many applications delete through GET/POST. Calls such as `curl 'http://t/api/user/delete?id=1'` could actually delete target data. A new `deny` rule covers `/delete /del /remove /unlink /erase /destroy`, requiring a delimiter after the verb so `/delivery`, `/details`, and `/delta` are not incorrectly blocked. Existing instances receive it on upgrade; it can be disabled or deleted under "System → Command interception".
- **Separate token accounting for model-fallback approvals**: approval calls previously had no independent usage attribution. Under the "Model fallback approval" switch in "System → Command interception", a new card shows cumulative calls, input, output, cache reads and cache writes, with a miniature daily-usage chart for the last 30 days.

### Finding notifications

#### Added

- **Push vulnerability findings to IM**: the new "System → Notifications" page supports **DingTalk, Feishu, WeCom, generic Webhook, Telegram, and email**. Configure any number of instances per type, each with independent enablement, rate limits and filters.
- **Realtime and digest delivery**: realtime sends matching findings individually; digest mode combines them at a global interval (30 minutes by default), headed by new-finding counts and severity distribution. Configure two channels for "high severity immediately, everything else in digests".
- **Four filter dimensions**: minimum severity; task/asset scope; included and excluded vulnerability-class keywords (exclusion wins); and whether to receive disposition-status changes (off by default). Messages include a "View details" button using the global backlink address, omitted when that address is empty.
- **Delivery history and manual resend**: shows status, attempt count, failure reason and channel for each delivery, filterable by channel and status. Failed items can be resent with one click, resetting their retry count.
- **Masked channel credentials**: webhook addresses, signing secrets, bot tokens and SMTP passwords are returned only as masked values with a suffix hint. Resubmitting the unchanged mask preserves the value; clearing the field deletes it.

#### Fixed

> These findings came from a security audit after the initial implementation. Each was reproduced, fixed and covered by regression tests.

- **Fixed a severe mask bypass through "change destination, retain credentials"**: configuration merging retained database values for omitted keys. Changing only the address could silently send stored credentials to an arbitrary destination. A destination change now requires an explicit choice for every credential field: provide a new value or explicitly leave it empty to remove it.
- **Escaped untrusted content in Markdown channels**: model-produced titles/summaries and scanned asset URLs could render as clickable external links in DingTalk, WeCom and Feishu; image syntax could trigger client fetches exposing readers' IP addresses. Rendering now normalizes to one line and escapes Markdown metacharacters at each channel's rendering boundary, not in the shared title function.
- **Restricted delivery-address SSRF**: previously loopback, internal and cloud-metadata addresses were all reachable, and the first 200 bytes of failure bodies appeared in `last_error` and delivery history, providing a semi-blind read primitive. Protection now runs at **dial time**, covering DNS rebinding, and rejects cross-host redirects. Loopback and link-local targets require `ARTEX_NOTIFY_ALLOW_LOCAL=1`; RFC1918 private addresses remain allowed for common internal SMTP relays.
- **Redacted notification credentials from errors**: DingTalk `access_token`, WeCom `key` and Telegram bot tokens occur in URLs. Failed deliveries exposed complete URLs in `last_error`, delivery history, server logs and UI notices. Errors now retain only `scheme://host` and the underlying cause.
- **Prevented silent loss when truncated digests marked the whole batch delivered**: omitted findings previously appeared neither in the message nor failures, while history showed success. Digests now pack **whole findings**, mark only included items delivered, and requeue the remainder without spending retry attempts. The header states how many will continue in the next message.
- **Validated minimum-severity values**: a typo such as `hgih` in `min_severity` previously assigned rank zero and effectively accepted every finding. Writes now validate the value and list supported options.
- **Made `rate_per_min = 0` (unlimited) reachable**: database writes silently replaced explicit zero with the default. Defaults now apply at the API layer only when the field is absent.
- **Applied token-bucket limits to digest channels**: `rate_per_min` previously had no effect in digest mode. Claim counts now respect both the current allowance and the memory limit.
- **Handled retry exhaustion per delivery rather than per-batch maximum**: an older item with two attempts could previously force fresh items in the same batch into permanent failure before they received any retry. Each item is now decided independently.
- **Rejected unparseable snapshots in digest batches**: these entries were skipped during rendering but marked successful with the batch. They now fail explicitly with a reason.
- **Bound per-channel delivery rounds by the lease duration**: in multi-instance deployments, expired leases could be reclaimed, producing duplicate sends and double-counted attempts. The round limit is derived from lease duration divided by per-attempt timeout; assertions lock down these relationships.
- **Avoided splitting HTML entities during Telegram truncation**: truncation already avoided partial tags but could leave fragments such as `&amp`, causing the parser to reject the **entire** message. It now avoids both.
- **Distinguished temporary SMTP failures**: 4xx replies such as greylisting `450` should retry, but were previously treated as permanent, causing every delivery to a greylisting server to fail on its first attempt. The first response-code digit now determines retryable 4xx versus permanent 5xx failure.
- **Rejected nested mask sentinels**: object fields such as `webhook.headers` must be masked or submitted as a whole. A mask sentinel inside the object was previously stored literally, silently breaking authentication. Such submissions are now explicitly rejected.

#### Design notes

- **Finding transactions perform one blind INSERT**: `notification_events` is inserted in the same transaction as the finding for atomic consistency. The INSERT intentionally does not read channel configuration or run user filters and is wrapped in a `SAVEPOINT`; a misconfigured filter must not prevent a high-severity finding from being saved.
- **Delivery uses leases, not long transactions**: `FOR UPDATE SKIP LOCKED` claims rows, sets them to `sending`, and moves `next_attempt_at` into the future as a lease. Delivery holds no database lock. Rows left by a crash become claimable after lease expiry, recovering without infinite retries.
- **Rate limits do not spend retry budgets**: calculate the token-bucket allowance first, then claim that many items. Reversing the order would count rate-limited waits as attempts and exhaust the three-attempt budget without sending.

### Test infrastructure

#### Fixed

- **Fixed permanent test artifacts from company ICP-ownership cleanup ordering**: `defer d.Close()` ran before `t.Cleanup`, so cleanup statements used a closed connection and discarded their errors. Test assets/companies remained permanently and caused unrelated asset-count assertions to fail. Connection closing now also uses `t.Cleanup`, registered first, and cleanup failures are surfaced.

### Traffic

#### Added

- **Content search, path/status/length filters and column sorting**: filter by **response content** through the full-text index, **path**, **status code** (exact or buckets such as `2xx`), and **response-length range**. Sort by time, status or response length; sorting preferences persist locally. Two new indexes apply automatically on upgrade; the Agent tool query contract is unchanged.
- **"Clear all" traffic action**: deletes all traffic regardless of filters, removes historical host directories no longer tracked by the index, then performs full compaction and reports actual freed space. Traffic already bound to findings lives in an independent evidence store and is unaffected.

#### Fixed

- **Reclaimed disk space after traffic deletion**: the index database lacked `auto_vacuum`, so its file never shrank. Its `contentless_delete` full-text index only added tombstones without reclaiming original postings, making deletions grow the index (measured: 6 MB captured body → 16 MB index, still 16 MB after deletion). New indexes use `auto_vacuum=incremental`; deletion starts background, chunked full-text merges and incremental reclamation, returning space gradually (104 KB in the same example). Write locks are released between chunks so recording continues.

> Upgrade note: `auto_vacuum` must be set when creating the database, so **existing indexes retain the old mode** and incremental reclamation is a no-op (noted at startup). Upgrading stops further tombstone growth. Run "Clear all" once to reclaim existing space and convert the database to incremental mode; subsequent ordinary deletes then reclaim automatically.

### LLM

#### Fixed

- **Applied custom session headers to one-shot LLM calls**: initial goal decomposition (round zero) and cold-node compaction do not attach transcript stores, leaving no session ID in context and no header at the gateway. Endpoints such as opencode zen that reject missing headers with 400 showed "round zero failed, later planner rounds normal". Both paths now receive an exploration-stable session ID, also restoring token attribution to that exploration, which was previously missing entirely.

### Tools

#### Fixed

- **Applied output truncation to MCP and custom tools**: MCP responses bypassed truncation completely, while HTTP custom tools received an empty tool context and used a fixed 6,000-character limit instead of session configuration. Both now use `actool.Capture` with session `MaxOutputChars` (default 30,000). When `ToolOutputDir` is configured, complete output spills to disk and the message retains a prefix and file pointer.
- **Upgraded norma to v0.4.3**: the harness now applies `CaptureOnce` uniformly, so tools, including future additions, cannot overflow context by missing truncation wiring.

### Deployment

#### Fixed

- **Fixed activity streams behind reverse proxies**: SSE URLs previously always used `${hostname}:8787`. Deployments served through HTTPS port 443 therefore tried to connect to a nonpublic port and repeatedly reconnected. Production now defaults to same-origin according to `NODE_ENV`; only `next dev` keeps `:8787` to avoid Next.js SSE buffering. `NEXT_PUBLIC_SSE_BASE` remains available. The README now documents reverse-proxy deployment.

### Findings

#### Fixed

- **Restored scrolling for overflowing asset lists in the "By asset" view**: the outer card had only `max-height`, leaving percentage-based viewport height unresolved. Lists overflowed or were clipped without scrolling. The height limit now applies directly to the asset tree's native scroll container and adapts to window height.

### Database

#### Fixed

- **Capped the connection pool**: `database/sql` previously opened unlimited application connections until PostgreSQL rejected them at `max_connections` (default 100). The cap is now 32; excess queries wait for an idle connection, slowing down rather than failing under the same load. Reduce this cap too if PostgreSQL's maximum has been lowered.
- **Improved connection reuse**: default `MaxIdleConns=2` closed connections above two after use, requiring new TCP and PostgreSQL authentication handshakes later. Idle capacity now matches total capacity, with a 30-minute connection lifetime and five-minute idle eviction to replace long-lived unhealthy connections naturally.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)

## [0.3.14] - 2026-09-24

### Task list

#### Added

- **New "Findings" column**: shows critical/high/medium/low counts, coloring nonzero counts by severity for a quick view of each task's finding volume and distribution.

#### Changed

- **Narrower "Description / Goal" columns**: long content is truncated with full text on hover, reducing horizontal space consumed by long descriptions.

### Exploration graph and planning overview

#### Changed

- **Slimmer `graph_overview` with list limits**: recent facts, completed intents, pending intents, cold digests and confirmed-finding details use a recent window plus total counts. Omitted items remain queryable on demand. This significantly reduces per-round LLM context and long-task growth; linked-task cold overviews are limited too.

#### Fixed

- **Removed reverse duplicate edges between digests and findings after folding cold nodes**.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)

## [0.3.13] - 2026-09-19

### Asset interception

#### Added

- **Global asset interception rules (blocklist)**: exact/fuzzy domain, IP and URL matching plus CIDR networks, with CRUD and enable/disable controls under "System → Asset interception". Built-in fuzzy rules block government (`.gov` / `.gov.cn`) and education (`.edu` / `.edu.cn`) websites by default.
- **Asset interception in execution paths**: before `add_intent` and `insert_assets`, Agents check target assets. Blocked intents are not issued and blocked assets are not inserted; asset details and the reason are returned to the Agent.
- **Task-level block/allow rules (allowlist)**: separate from global rules and limited to the current task. Evaluate blocks first, then allows: a block match forbids testing; if there is no block match but configured task allow rules all miss, testing is not allowed. With no allow rules, the allowlist is disabled. Rules can be entered during task creation or managed in the task Overview.
- **Task templates include category and task block/allow presets**: saved categories and rule sets populate the new-task form when the template is applied.

### Operation review

#### Fixed

- **Tightened model-judge output to reduce truncation-related fail-open decisions**: reduced comment length from 500 to 120 Chinese characters and required JSON only, without preambles or code fences. This reduces unparseable `MaxTokens`-truncated verdicts that would otherwise follow the model-failure allow policy.

### Task archives

#### Fixed

- **Skip symlinks instead of failing the entire archive**: the archive format supports only regular files and directories. A symlink previously failed the whole task archive. It is now skipped and logged while other files are archived, without following links or leaving the directory tree.

### Accounts and compliance

#### Added

- **Pre-login usage notice and disclaimer dialog**: agreement must be checked before logging in.

### Licensing and dependencies

#### Changed

- **Adopted the AGPL-3.0 open-source license**, with improved README licensing and disclaimer documentation.
- **Upgraded norma to v0.4.1**.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)

## [0.3.12] - 2026-09-17

### Exploration activity feed

#### Added

- **Exploration activity feed on task details** (#144): a chronological stream of exploration nodes (start/goal/intent/fact/finding/hint/digest), with type filters, keyword search, ascending/descending order, pagination and automatic refresh. The first page in newest-first mode is live and refreshes every cycle; elsewhere, updates only increase an unread count without interrupting reading, with "N new updates · Back to latest" for one-click return. Day grouping keeps dates recognizable while paging through long tasks.
- **Node IDs on every feed row** (#145), for cross-referencing the exploration graph and locating specific nodes.
- **Expanded node details show upstream/downstream relationships and anchored assets** (#147): hover related entries to see cards with type, status, source, time, summary and payload excerpts. Anchored assets include type tags and recognizable text. Related data arrives with the feed page, so expansion sends no additional requests.
- **Node-ID search** (#150): besides content/source search, enter a number or displayed form such as `#41` to locate the exact node.

### Intent management

#### Added

- **Soft and hard intent deletion** (#149): queued, running and paused intents can be deleted with a required reason; select the mode in the confirmation dialog.
  - **Soft deletion (default)**: marks the intent deleted and stores the reason separately, retaining its node, all outputs and lineage.
  - **Hard deletion**: physically removes the intent and descendants supported exclusively by it, cascading output/intent chains to the leaves to avoid orphaned data. Deleted intents' token accounting remains archived under original dates. Shared nodes referenced by other intents, goals and task-root facts are always retained. The hard-delete dialog previews the cascade node count.

  Both modes notify the planner that the user deleted the intent, include the reason, and trigger replanning.

### Operation review

#### Added

- **Filter approval records by status and decision source** (#139), to locate relevant records quickly.

#### Fixed

- **Load pending approvals independently of history pagination** (#133): pending requests remain fully visible while browsing historical pages.

### Agent

#### Changed

- **Worker role description now uses general cybersecurity-platform wording** (#138).

#### Fixed

- **Restored cold-node compaction**: the task planner now wires in the Compactor; cold-digest compaction previously never ran because it was not connected.

### Chat

#### Added

- **@ references to multiple record types with scrolling pagination** (#135): reference candidates load incrementally while scrolling.

#### Fixed

- **Constrained long message bubbles to the conversation panel** (#137).
- **Fixed "missing upload file" before a new conversation exists**: the Composer snapshots `FileList` into an array before clearing the input and invoking the callback. Draft uploads first create a conversation asynchronously; previously the live-linked `FileList` was empty when execution resumed, omitting the upload field and producing backend 400 errors.

### Traffic

#### Fixed

- **Recording proxy now binds only to 127.0.0.1 by default** (#129, #130), avoiding an open proxy exposed on other interfaces.

### Web

#### Fixed

- **Restored the global header on the static-export task list**.
- **Added linked-traffic mocks to demo finding details**, fixing a blank screen.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)
- [@RuoJi6](https://github.com/RuoJi6)
- [@dingpotian](https://github.com/dingpotian)

## [0.3.11] - 2026-09-15

### Operation review

#### Added

- **Navigate approval records to their source** (#125): clicking Source opens the original complete conversation, automatically pages to the exact tool execution, expands its command/results and centers/highlights it while retaining surrounding context. Manual scrolling stops automatic correction. Ordinary chat, task Workers, planner and segmented main-Agent sessions use persisted `tool_use_id` and task mapping, with a new scoped call-ID index. Missing, duplicate or ambiguous associations explicitly warn instead of opening another execution. Deleted conversations, archived tasks and absent records receive clear explanations.
- **Approval-record pagination** (#115).

#### Changed

- **Simplified model-review input** (#124, #125): fallback-judge input is versioned JSON containing the complete current tool call, explicitly selected short context and the local working directory. Context uses only actual user messages from chat or the task main Agent. Workers no longer include intent summaries or inherited parent-Agent context; planners and automatically triggered sessions do not invent user messages. Task descriptions, goals, constraints, global exploration state, historical calls and complete Worker intents are excluded from operation review, while still serving normal Agent execution and independent session auditing. Verdict JSON requires `decision`/`comment`; explanations must identify the actual operation, consequences if successful and matched rule. Instructions inside arguments/context cannot change review policy. Actual model inputs and fingerprints are saved; old snapshots retain their versions and are not reconstructed from current data.

#### Fixed

- **Code-fenced verdicts no longer silently allow execution** (#126): fenced JSON previously failed parsing and could be allowed by model-failure policy. Fences are now stripped before parsing; configured failure behavior applies only if parsing still fails.

### Agent

#### Added

- **Experimental noa context compaction** (norma v0.4.0): optional in system settings, disabled by default. When enabled, model-driven noa replaces built-in compaction for main Agent, planner, Worker and chat. Original compacted content is archived centrally at `<workDir>/noa/<sessionID>/`, with globally unique per-session directories rather than separate task directories. Integration failure falls back to built-in compaction without interrupting tasks. The switch is read once per run and affects only subsequently started runs.

#### Fixed

- **Exploration graph tools reject calls without task context rather than dereferencing nil stores**.

### MCP

#### Added

- **Legacy SSE MCP support** (#117), for servers offering only that transport.

### Traffic

#### Fixed

- **Port-aware host matching in traffic search** (#114): `traffic_search` distinguishes records for the same host on different ports.
- **Isolated `traffic_search` description-migration failures**, so subsequent reporter migrations still run.

### Web

#### Changed

- **Separators between finding-retest options** (#122), improving visual clarity.

#### Fixed

- **Fixed demo task-details page crashes**: the Sessions tab requested `GET /api/tasks/<id>/side-questions`, missing from mock handlers. The fallback treated paths ending in `s` as collections and returned `[]`, leaving `data.items` undefined. The side-question hook's `merge()` then threw `TypeError: t is not iterable`. Because this happened inside a `setItems` updater, React rethrew during rendering, bypassing the caller's catch and replacing the page with "This page couldn't load". The mock now explicitly returns empty side-question history; `sideAPI.history` additionally normalizes non-array `items` to `[]`.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)
- [@RuoJi6](https://github.com/RuoJi6)

## [0.3.10] - 2026-09-13

### Network

#### Added

- **Official DeepSeek web-search source**: reuses the active LLM configuration. Unlike the other three sources, DeepSeek has no directly callable search API; search exists only as the `web_search_20250305` server-side tool inside its Anthropic-compatible interface. It therefore **requires an official DeepSeek endpoint and Anthropic protocol**; OpenAI-protocol endpoints reject server-side tools. Each search **costs an additional model call**, **bypasses the search outbound proxy**, and **is not included in traffic capture**. Results contain **titles and links only**, without snippets; WebFetch retrieves page bodies when needed. Settings explain these limits but **do not enforce validation**. Users must verify compatibility, optionally using Test search.

### Agent

#### Added

- **Cross-work trace review for Workers**: `search_all_worker_traces` searches matching steps across all work in the current task without needing an `intent_id`; `get_worker_trace` lists a selected work's steps, searches within them, or retrieves complete content by `step_id`. This reuses observations other work saw but never recorded as facts, avoiding duplication.
- **Workers receive `node_detail`** to retrieve complete intent/node details alongside trace review.
- **Explicit planner notification for `add_hint`**: hints previously only appeared in the overview for planners to notice. Each call now records one "User added N strategic hints: …" trigger, treating batches as one event rather than flooding individual notifications. The planner sees that new hints triggered the round and receives their content directly.

#### Changed

- **Global overview prompt encourages broader exploration**, timely reporting of cross-intent leads and less premature convergence.
- **Simplified Worker boundary wording**: an initial obstacle does not mean exploration is complete; exhaust approaches within the current intent before concluding.
- Removed per-asset `related` from `insert_assets`. It only decided whether an asset entered task scope and was not persisted, so re-registering invalidated the decision and the UI could not show which assets were considered unrelated. It imposed an extra model judgment with no durable record.
- **Task scope no longer depends on the coverage switch**: automatic `task_scope` insertion with `source='auto'` always runs, and `add_task_scope` remains available to planner, task main Agent and goal decomposition. Scope defines authorization boundaries and query filtering; coverage controls whether it becomes a metric denominator, not whether scope is accumulated. Disabling coverage previously disabled both `auto` and `agent` write paths, leaving only manually entered rows. `list_untested_assets` still hides when coverage is off because it is purely a coverage tool.

#### Fixed

- **Fixed cross-task asset leakage** (#59): `list_assets` previously hard-coded task ID zero and queried the entire shared store without giving models a way to express scope filtering, causing unrelated assets, especially IPs, to become test targets. It now returns only assets belonging to the scopes of the **current task and directly linked tasks**, matching ownership rather than literal values: a root domain includes its subdomains/services/endpoints, and a network includes hosts/services in it. Direct lookup by ID also excludes out-of-scope assets. Non-task Auto/pentest contexts still fall back to the global store. Also fixed direct-IP hosts such as `http://1.2.3.4/api` not matching network ownership. The UI's producer-filtered Test assets view is unchanged.
- **Restored accidentally unbound cross-work tools**: an old "reduce Worker tool surface" startup migration unbound `search_all_worker_traces`, `get_worker_trace` and `node_detail` after they had been added to defaults. They were removed from that unbinding list, with a one-time rebinding migration for affected databases.
- **Fixed intermittent `/btw` persistence failures**: strip JSONB-unsupported NUL (`\u0000`) escapes before saving side-question checkpoints.

### Triggers

#### Fixed

- **Deduplicated task descriptions/goals by task in merged triggers**, avoiding repeated long goals inflating context when multiple task triggers combine.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)

## [0.3.9] - 2026-09-11

### Agent

#### Added

- The task's main Agent supports **multiple conversations**: create, switch, and reset context independently; every conversation is interactive.
- Added persistent `/btw` **side questions** without interrupting the main follow-up conversation. Questions and answers are stored in the database and survive restarts.
- `spawn_task` accepts `source_task_ids`, allowing new tasks to **inherit read-only** assets and findings from source tasks.

#### Changed

- Disabled workers' cross-engagement memory and narrowed their default tools: reading context and reviewing other work belong to planning; workers execute and write back a single intent.
- Simplified the default worker prompt; removed `terminated` and `worker_name` from `get_worker_output`; shortened `insert_assets` / `list_assets` descriptions.

#### Fixed

- Fixed work ending prematurely after an **idle turn**. Sometimes a model spends a whole turn thinking without producing text or calling tools. The harness sees a natural end (`end_turn` without `tool_use`) and finishes as `completed` with an empty summary, leaving an unfinished intent halfway through: a normally completed task with no textual summary. None of the five LLM retry layers catches this: it is not an error; same-provider safe-window retry requires a stream failure; the breaker treats `err == nil` as success; intent retries recognize only `model_error`. The SDK's empty-response retry also misses it because it tests whether any event was yielded, and thinking deltas are events. The work Stop hook now identifies these turns and injects a continuation instruction so the model proceeds using its existing thinking. It deliberately does not resend unchanged: prompt/context shape often makes this stable behavior rather than random failure, and resending merely repeats the thinking.
- Reused the **empty-response retry count** under the LLM page's Retry and backoff settings. Both address a model finishing without substantive output, but with different tests and mechanisms. Default: 2; `-1` disables it and restores completion on idle turns. This is a **total per-intent** limit, not a consecutive-turn limit: the harness already permits only one consecutive idle-turn nudge, refreshing that quota only after a real tool turn. This setting prevents loops such as tool → idle → nudge → tool → idle from consuming the entire intent budget. Trigger logs identify `[work <worker> · #<intent>]` and record the idle turn and continuation instruction `(n/N)`. See [the LLM retry design document](docs/LLM%E9%87%8D%E8%AF%95%E8%AE%BE%E8%AE%A1.md), §1.1.
- Fixed `/btw` context budgeting and input layout in long conversations; added fallback side-question request-ID generation in insecure (non-HTTPS) contexts.

### Traffic

#### Added

- Findings can **link multiple traffic evidence records**, with ordering, notes, and roles (request/response/corroboration).
- The report Agent **automatically links** relevant traffic evidence before writing a report.
- Added an Agent **traffic-binding switch** and completed evidence handoff between Agents.

### Tasks

#### Added

- Added **conversation-level finding retests**, running in an independent Agent conversation with execution status in the list and details.

### Interception

#### Added

- Approval records now include **details and execution audits**: tool-request context, initial model/rule judgment, execution output, and argument fingerprints.

### Assets

#### Added

- Task test assets support **DSL search**.

### LLM

#### Fixed

- Connection tests now include the custom session header, fixing opencode zen HTTP 400 responses.

### Network

#### Added

- MCP HTTP transport can **skip TLS certificate verification**, supporting services with self-signed certificates.

### UI

#### Added

- Conversations are grouped by Agent, with expand/collapse, independent pinning, and Agent filtering.
- Attack-path graphs render digest nodes and collapse their members.

#### Fixed

- Fixed a blank screen caused by unsynchronized login credentials.
- Interception messages explicitly identify platform policy so they cannot be mistaken for target defenses.

### Deployment and updates

#### Added

- Added **one-click updates in the UI**: a Version and updates card in system settings and a top-bar new-version indicator. The sequence is download release → verify `SHA256SUMS` → smoke test → stage → exit and let the supervisor relaunch the replacement; the page refreshes automatically.
- Added `start.sh` / `start.bat` supervisor launch scripts as the official entry points, included in release archives and Docker images; `install.sh` is unchanged. Exit codes determine relaunch behavior, and SIGTERM is forwarded to artex. Verification and replacement remain in Go, keeping scripts simple.
- Automatic fallback: failed verification or smoke tests discard the update and keep the current version running; three consecutive startup failures of the new version roll back to the previous version. Settings also offer manual rollback; database schemas are not rolled back.
- Updates require HTTPS and GitHub domains; the release source is not configurable. Development builds disable one-click updates. GitHub queries are cached for 30 minutes to prevent top-bar checks exhausting API quotas.

#### Known limitations

- Docker updates replace the executable, not the image. The toolchain is not upgraded, and recreating the container restores the image's bundled version; use `docker compose pull artex` when needed.
- Release `skills/` contents are not synchronized, so newly bundled skills are not automatically installed.
- Updating restarts the process and interrupts running tasks.

### Dependencies

#### Changed

- Upgraded norma to v0.3.7.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)
- [@RuoJi6](https://github.com/RuoJi6)

## [0.3.8] - 2026-09-09

### LLM

#### Added

- Added a Retry and backoff tab to the LLM page, configuring both **counts** and **intervals** across five retry layers. From inner to outer: connection retries (SDK; resets/timeouts/429/5xx before streaming); empty-response retries (SDK; normal completion without content, OpenAI format only); same-provider safe-window retries (replay a failed stream before delivering any output); pool circuit breaking (cool down and skip after consecutive failures reach the threshold); and intent reruns (rerun the whole intent after a worker ends with `model_error`). An outer layer acts only after the inner layer is exhausted. Each layer has two consistently interpreted controls: blank preserves default counts/exponential backoff; a count overrides the count; an interval replaces exponential backoff with a fixed interval; `-1` disables that retry layer. The first three follow the endpoint and can override global defaults per field in each model profile; overriding only an interval still inherits the global count. Circuit breaking and intent reruns are process-wide and have only global settings. Saves take effect immediately without restarting. Leaving everything blank preserves existing behavior byte-for-byte after upgrades: new columns default to 0 and absent settings keys use defaults. Connection tests deliberately exclude these settings because their hard 30-second timeout could misclassify working endpoints when combined with configured retries. See [the LLM retry design document](docs/LLM%E9%87%8D%E8%AF%95%E8%AE%BE%E8%AE%A1.md).
- Each LLM profile supports a custom **session header** (`session_header_key`). When nonempty, every LLM request includes this HTTP header with the current run's session ID (`conv-<id>` for chat, `exp<x>-worker-i<intent>` for workers, etc.), supporting gateways that use session headers for prompt caching or sticky routing. The ID is injected from request context without changing norma; a shared provider can send different header values per conversation. Includes existing-database migration and an input in the profile dialog.

#### Fixed

- Fixed `23502` errors when saving profiles with an empty session-header field. The column is `NOT NULL DEFAULT ''`, but `NULLIF($n,'')` incorrectly converted an empty value to NULL. Values now retain empty-string semantics.

### Agent

#### Added

- Wall-clock timeouts now **wrap up in place** using norma v0.3.6. When `MaxDuration` expires, the current tool is interrupted and wrap-up rounds continue with a live context to write recognized results and a summary, ending as timeout. This replaces worker/planner `maxDur+90s` external hard contexts that killed stuck runs as `aborted_tools`. Chat inherits the behavior through the same harness; mainagent has no `MaxDuration` and is unaffected.
- Added stall recovery: if a heartbeat/no-change wake finds no open or running intents anywhere in the graph, the planner opening message warns that no workers or queued directions remain and requires one or more distinct new intents, never zero.

#### Changed

- Refactored worker prompts: intent, startup instructions, and raw anchored-asset JSON move into the system prompt, rebuilt every round so compaction cannot remove them and resume no longer depends on the transcript's first message. The initial user message contains only global overview, which may be degraded or stale. Mixing per-intent data into system context sacrifices cross-intent caching deliberately to preserve the intent.
- Simplified planner/worker default text and corrected practical behavior: planners distinguish `recent_done` states, reviewing traces before deciding on blocked/exhausted work instead of treating it as a dead end or blindly rerunning it; negative conclusions are observations/tentative interpretations, not final verdicts, and require evidence review. Unmet goals with no open/running intents require new output; depth takes priority over breadth. Workers record negative conclusions only as observations plus tentative interpretations, leaving judgment to the planner; cross-intent leads go in fact summaries rather than being pursued independently. Reseeding appends and selects new default prompt versions, preserving customized/older versions for rollback.
- Narrowed worker default tools and asset-writeback instructions. Workers execute and record one intent; planners handle contextual reading and cross-work review. Removed `list_facts`, `node_detail`, `list_companies`, `search_all_worker_traces`, `list_worker_traces`, and `get_worker_trace` from worker defaults, retaining `list_findings` for deduplication before reporting, `add_finding`, `record_fact`, `insert_assets`, and `list_assets`. Removed stale `type=tech`/`on_url`/`props` guidance conflicting with `insert_assets` schema. A one-time migration removes only worker bindings, preserving planner/main bindings.

### Exploration graph

#### Added

- Added **cold-node digests**: old, long-inactive intents/facts are folded into digest nodes in `graph_overview`. Original nodes remain permanently and can be restored by ID: storage is lossless; only presentation is compressed; folding is reversible. Hot/cold classification uses reverse reachability, treats a node as hot if any branch is active, debounces for R=6 rounds, and groups connected components. Background minor compaction folds uncovered cold components; major compaction rereads originals and merges fragments. Activity rechecks, cooldowns, and mutual exclusion keep compaction off hot paths and prevent overwriting revived nodes. Overview exposes `cold_digests` and asset-indexed `cold_index`; `expand_digest` / `expand_index` restore them. Related/inherited tasks reuse their own folded views; `expand_digest` supports read-only cross-task restoration. These tools belong only to planner/main Agent, not workers. Includes existing-database migrations.
- `graph_overview` now includes the complete `finding_list`. Findings are high-value and usually few per task, unlike facts, which retain a recent window. Planner/worker can see every confirmed finding each round without calling `list_findings`. Entries are compact `{id, summary, evidence?, from_intent?, assets?}`; affected assets show readable URLs/domains/IP:ports rather than bare IDs.

#### Changed

- Removed flattened `hosts` from coverage overview: carrying up to 500 host strings each round in broad tasks contributed little to planning. `host_count` remains; use `list_assets` for details. Added top-level `done_intents_total` beside the ≤15-entry `recent_done_intents`, so planners know whether deduplication context was truncated.

### Tools

#### Changed

- Changed the default `add_company_scope` binding from worker to planner: defining enterprise scope belongs to planning/main/Auto, while workers explore. New databases bind mainagent/planner/auto; existing databases receive a one-time migration.
- Planners now default to `list_assets`, supporting both full-database DSL search and `list_untested_assets` for untested in-scope assets. A one-time backfill does not overwrite user unbindings.

### Tasks

#### Added

- Task lists now show Running Workers, counting intent nodes with `state='running'`, exactly matching task-detail overview; zero is displayed when none run.

### Network

#### Added

- Added a **global outbound proxy** for target traffic (http/https/socks5 with `user:pass`). With capture enabled, it is the MITM recorder's upstream: traffic is recorded then proxied; interception and passthrough both use it without leaking the source IP. With capture disabled, it is injected directly into Agent bash environments and WebFetch; `proxyEnv` adds `ALL_PROXY` for socks5. Configuration uses the settings KV table without migration. A Global proxy card in system settings is independent of web-search and LLM proxies.

### UI

#### Fixed

- Corrected intent-state labels: `exhausted` now means Budget exhausted, not Exhausted exploration, because step/time budgets interrupt work after partial results rather than proving a direction exhausted. `blocked` now means Execution error, not Intercepted: it represents exhausted model/API/network retries, not target/WAF blocking. Added the missing `stopped` label for user-stopped work.

### Dependencies

#### Changed

- Upgraded norma to v0.3.4, adding MCP output truncation and disk storage (see `651b961`); subsequent v0.3.6 enables in-place wall-clock wrap-up.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)

## [0.3.7] - 2026-08-31

### LLM

#### Added

- Model profiles now configure an Output limit and the request field carrying it. The limit caps generated tokens per response and is sent with every request; 0 (default) omits the field, leaving the server default. This differs from Context window, the model's total capacity used only for local compaction thresholds and never sent. Field selection applies only to `openai` Chat Completions: empty defaults to `max_tokens`, recognized by most compatible gateways. Official OpenAI reasoning models (o-series/GPT-5) instead require `max_completion_tokens` and reject `max_tokens` as `unsupported_parameter`, so these endpoints need manual selection. Anthropic always uses `max_tokens`; Responses always uses `max_output_tokens`; non-openai profiles disable and clear the selector on save. Also connected a previously missing path: planner/worker/chat/main Agent/goal decomposition had never set output limits, so OpenAI omitted them and Anthropic fell back to the SDK's 8192. Adding configuration alone would not have worked. Limits now resolve each round like streaming preferences and change on the next round after failover. Existing databases retain behavior because new columns default to 0/empty.
- Profile Test now logs each HTTP attempt's status and raw gateway response body (capped at 4K), helping diagnose 401s, credit messages, empty frames, and HTML responses beyond the UI's summarized ok/error. Independent of the LLM recording switch.

#### Changed

- Task LLM chains can be edited in every state, not only running/paused/exhausted. Main Agent conversations still use the chain after done/failed/timeout, and a broken model previously prevented further interaction without allowing changes. Removed terminal-state restrictions from HTTP and DB transaction paths, and enabled editing/saving in the dialog. Saving terminal tasks no longer reopens credit-blocked intents, which would otherwise become open with no workers and no longer qualify for rerun. Continue through rerunning an intent or adding a goal, which restores running state.

### Agent

#### Changed

- Steering a running Worker now reuses pause/resume and transcript resume, matching main Agent conversations, rather than a custom intervention persistence protocol involving scheduler barriers, recovery, and more than ten activity-query filters. Messages enter the next input round; intents run in dedicated goroutines outside the three-slot worker pool, starting immediately. The UI retains the message field, restores Continue directly, and sends through SSE. No DB schema changes. Tradeoffs: messages are in memory without crash recovery, and a message can transiently exceed task concurrency by one worker, accepted for this infrequent operation.

### Tasks

#### Fixed

- Fixed OOM crashes archiving large tasks by streaming snapshots rather than loading entire tasks into memory. Also closed three cold-archive recovery gaps: modern traffic exists only in SQLite, and a crash between PostgreSQL and SQLite commits could strand archived traffic in hot storage without recovery. Staging journals are now written regardless of legacy directories, and missing `journal.json` is treated as discardable.
- Fixed slow conversation/page loading with many tasks: optimized task-list, task-context, and exploration-record queries and reduced duplicate dashboard/conversation/task-detail requests.

### Assets

#### Changed

- Removed the 256-rule enterprise-scope limit from backend, frontend, and demo mocks. Enterprises entering individual IPs/domains often exceeded it and had to split into multiple enterprises despite rule count not inherently slowing scope handling. Individual rules remain limited to 1024 characters; the 2 MiB request-body limit remains as a safeguard, roughly 40,000–50,000 rules.

### Skills

#### Fixed

- Fixed `Upload failed: zip: unsupported compression`. Go's standard library supports only Store/Deflate, so bzip2/Zstandard archives from nondefault compression settings failed. Added pure-Go decompressors without new external dependencies. Unsupported Deflate64/LZMA/XZ/PPMd and encrypted archives now produce a pre-extraction explanatory message identifying the file, compression method, and repackaging instructions rather than exposing a low-level English error. These messages were originally presented in Chinese.
- Fixed rejection of Chinese skill filenames by the previous ASCII `[A-Za-z0-9-_./]` allowlist, which rejected an entire archive if any filename used Chinese, such as a translated equivalent of `reference/instructions.md`. Replaced it with a Unicode denylist: multilingual filenames/spaces are allowed, but controls, invalid UTF-8, zero-width/bidirectional controls (RLO spoofing), `\ % # ? * : " < > |`, `..`, absolute paths, and empty path segments remain forbidden. Traversal protection is unchanged. Skill names also permit non-ASCII letters, including Chinese; ASCII still requires lowercase letters/digits/hyphens under agentskills.io, and spaces/dots/path separators remain forbidden.
- Fixed garbled or rejected Chinese filenames in Windows-created ZIPs lacking the UTF-8 flag: filenames now fall back to GBK decoding before validation. Quoted frontmatter names, such as the translated equivalent `name: "Chinese skill"`, are also parsed correctly.

### UI

#### Added

- Added a default flat All findings view, switching via header tabs with the existing Group by task view. Grouped mode required expanding each task even when users only wanted a cross-task overview. Flat mode uses a cross-task table with 10/20/50/100 rows per page and the same operations: select/export, inline evidence/report expansion, rename/class/severity editing, status updates, deeper investigation, and deletion. Both share statistics and filters; switching preserves filters and persists view/filter choices locally. Only the active view polls, and edits update both caches to avoid stale data after switching.
- Added an By asset view: a left tree (enterprise → root domain/IP → subdomain → service → endpoint) and findings from the selected subtree on the right, sharing the same table and filters. Enterprise nodes appear only when assets actually have ownership. The tree includes assets with findings and fills missing ancestor chains, even when findings exist only at endpoints. Counts aggregate subtrees and deduplicate findings. Deleted/unlinked assets go in an Unlinked assets bucket. Unlike the other views, it does not poll: queries run on entry, filter changes, in-page edits/additions/deletions, or tree refresh, avoiding needless five-second navigation-tree recalculation. Labels show only incremental values: subdomains omit the root suffix, services show `https :443`, endpoints show paths; tooltips/breadcrumbs retain full values. Excess nodes cause entire endpoint/service layers to be omitted with a notice, while counts remain included above.

#### Fixed

- Mobile conversation details now collapse the conversation list on narrow screens, giving history enough width to remain readable.

### Dependencies

#### Changed

- Upgraded norma v0.3.2 → v0.3.3: `Config.MaxTokensField` allows `max_completion_tokens` for reasoning-model Chat Completions. The two fields are mutually exclusive; default remains `max_tokens`.
- Upgraded `golang.org/x/mod` v0.37.0 → v0.40.0, fixing two dependency security alerts.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)
- [@neouks](https://github.com/neouks)
- [@begininvoke](https://github.com/begininvoke)

## [0.3.6] - 2026-08-27

### LLM

#### Fixed

- Fixed Non-streaming reverting to Streaming when reopening saved settings (#69). The list DTO omitted `streaming`, so the frontend read `undefined` and defaulted to streaming despite correct writes/storage. Restored the field without `omitempty` so `false` is returned.
- Connection tests now require an actual model reply and use the profile's real streaming/non-streaming mode (#65). Previously HTTP success alone passed even with no response content due to exhausted thinking budget, safety filtering, or compatibility layers dropping `content`; tests also always streamed, testing a different path for non-streaming profiles. Empty replies now fail; successful tests show the response and use the current streaming switch, aligning test success with usable conversations.

### Agent

#### Changed

- `list_facts` now paginates and filters keywords to avoid overflowing context (#74). Default: newest 20; `limit` max 100, `before` cursor, `q` summary keywords; output `{facts, total, has_more, next_before}`. Long summaries truncate by character count; `node_detail` retains full text. Worker/planner prompts reflect pagination. A one-time migration refreshes the tool-catalog parameter schema because `SeedTool` only inserts initially; otherwise tool management would display No parameters.

### Tools

#### Added

- Tool execution now offers usage statistics (#72): the Statistics dialog shows calls, percentages, and failures per tool in descending call order. It uses current task/keyword filters across the complete result set, not just the page, and fetches only when opened.

### Dependencies

#### Changed

- Upgraded norma v0.3.1 → v0.3.2: preserves previously dropped OpenAI reasoning/refusal output (adds Responses `reasoning_text`), deduplicates three reasoning field names, bounds empty-response retries (zero content blocks), and fixes gateway 400s from compaction splitting tool-call/result pairs, including existing orphaned entries already in transcripts.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)

## [0.3.5] - 2026-08-25

### LLM

#### Added

- Per-profile streaming/non-streaming selection, default streaming. Enabled uses SSE; disabled sends `stream:false` and receives complete JSON, bypassing faulty gateway SSE implementations with empty/dropped thinking frames at the cost of live progress/token counts. Worker/planner/mainagent/chat/goals resolve the active profile dynamically. `llmpool`, `llmrec`, and task-runtime Provider wrappers support non-streaming. Added/migrated `llm_profiles.streaming`, default `true` so old profiles are unchanged.
- Added `openai-responses` profiles alongside Chat Completions/Anthropic, using `POST /v1/responses`, normalized `BaseURL`, and default model `gpt-5`. Updated/migrated the format constraint idempotently and added OpenAI (Responses API) to the UI. Requires norma v0.3.1, including the `reasoning_content` return fix.
- Records raw LLM HTTP requests/responses at transport level, retaining tool schemas, `tool_use`, and original SSE frames absent from normalized views. Every internal norma retry attempt is retained separately. Added/migrated `llm_records.raw_request` / `raw_response`; details include Raw view and request/response copy buttons with insecure-context `execCommand` fallback.

### Conversations

#### Changed

- Replaced permanent row checkboxes with a Multi-select mode. The header shows total count plus a Multi-select button; entering mode enables checkboxes/select-all/bulk-delete. Done exits and clears selection; fully successful bulk deletion exits automatically, while failures keep selection mode for retries. Individual rename/pin/delete remain in row menus.

### UI

#### Changed

- Improved task management, conversation operations, and traffic viewing (#57): persisted task-column sort preferences, direct rename icons instead of menus, refined sheet interactions and traffic details.

#### Fixed

- Worker asset labels show only domains/IPs and correctly handle empty labels.

### Agent

#### Added

- Planner goal evaluation now includes **quantitative acceptance checks**. For goals requiring X% asset coverage, N flags, or a specific privilege, verify measured `graph_overview` values (`coverage.pct`, finding counts, etc.) before `prove_goal`. If unmet, dispatch intents to close gaps; do not mark met because the main work is approximately complete. Fixes 100%-coverage goals incorrectly completed at 40%.

#### Changed

- Rewrote when planners should emit zero intents. Previously calling it the most common/important principle encouraged premature stopping with unmet goals or untested scope. Zero is appropriate only when all candidate directions are already covered by open/running/recent_done intents, or the next step depends on still-running work whose output has not arrived; then wait until completion and the next graph-change wake. Conversely, genuinely new independent directions or unmet goals with untested surfaces must not be abandoned merely because zero is common.
- Strengthened evidence requirements for worker negative conclusions such as noninjectable, closed port, or no login entry, which can cause planners to abandon an entire direction. Exhaust reasonable in-intent approaches (encoding/parameter/path/method variations) first; incomplete methods or weak evidence require `confidence=inferred`, avoiding premature `observed` negatives that shut down routes, especially early errors that are difficult to recover from.
- One-time settings-flag-guarded `reseedPlannerPrompt` / `reseedWorkerPrompt` migrations append/select new default prompt versions. Old/custom versions remain restorable in history.

### Operations

#### Added

- Added `reset-password.sh` for the fixed `ARTEX` administrator username in local/docker deployments. Connection information is explicit or resolved from `--dsn` / `$ARTEX_PG_DSN` / `config.json`. PostgreSQL `pgcrypto` generates a login-compatible bcrypt hash and updates `settings.auth.password_hash`; no restart is required. Passwords pass via environment, not process argv, with escaping against injection.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)
- [@neouks](https://github.com/neouks)

## [0.3.4] - 2026-08-24

### UI

#### Added

- Selected tasks can change category in bulk, including Uncategorized to remove current categories. The batch writes in one transaction; failures only concern tasks deleted since selection.

#### Changed

- New-task category selection is now a searchable input: typing filters categories; Enter or Create immediately creates/selects a missing name. Selection appears as a removable tag; only one category remains permitted.

### Conversations

#### Fixed

- Fixed messages being blocked as LLM not configured when a conversation selected a profile but no global profile was active. The pre-send check previously examined only global activation while execution preferred conversation selection. Both now use conversation/Agent binding first, global fallback.
- Unavailable-chat guidance distinguishes no configurations (Add a profile) from configured but inactive profiles (Activate or assign one to this conversation), instead of always claiming unconfigured.

### Agent

#### Added

- For tasks whose goals are all met, intents submitted by the main Agent with `add_intent` can be claimed directly by workers and stop after execution. Planner does not run without open goals, avoiding reevaluation that cancels newly submitted intents. Once the frontier drains, the task returns to completed. Before submitting, main Agent asks whether the intent should become a formal goal; registering it restores normal autonomous planning.

#### Changed

- `get_worker_trace` / `get_task_worker_trace` no longer error when `step_ids` exceeds five. They return the first five complete steps and identify returned/omitted steps through `returned_step_ids`, `omitted_step_ids`, and `notice`, explaining that no further retrieval is needed if sufficient. Duplicate/invalid IDs are removed before counting.

#### Fixed

- Task lists sort by descending ID rather than creation timestamp. Simultaneously created tasks shared timestamps and unstable ordering, causing frequent position swaps with ten-second polling and random map iteration.

### Assets

#### Fixed

- Fixed enterprise-ownership recalculation failing on one malformed row. Hostnames in `assets.ip`, inserted by an Agent or API, made `a.ip::inet` throw `22P02`, rolling back enterprise scope additions/changes and enterprise deletion. Safe `try_inet()` conversion skips invalid values rather than aborting the statement.
- Invalid IP values are no longer silent: scope saves report the number of affected assets with IDs/values, warning that IP/CIDR ownership rules cannot classify them. The same details are logged for paths without frontend responses, including enterprise deletion, scopesentry sync, and Agent writes.
- Task-scope IP/CIDR matching also uses `try_inet()` instead of the character-set regex guard, which still allowed all-hex-letter hostnames such as `abc.def` to trigger the same error.

#### Changed

- `ip`, `service`, and `endpoint` asset IP fields reject hostnames. `insert_assets` and the asset API return per-item errors with `index` and correction guidance: use `type=subdomain` with `domain`, or resolve A/AAAA records first. Agents can correct/reinsert; other items in the batch still save normally.

### Contributors

- [@neouks](https://github.com/neouks)

## [0.3.3] - 2026-08-23

### Worker

#### Added

- Running workers support individual pause/resume/cancel. Pause preserves intents/facts/findings; cancellation transactionally removes the current intent and its direct products after execution exits.
- Added `paused` intent state, execution fences, and named termination reasons to prevent late blackboard writes after pause/cancel/task deletion.
- Added optional task concurrency limits; creation, resume, deeper finding investigation, and queue refill use a persistent FIFO admission path.

#### Changed

- Default worker run wall-clock duration increased from 600 to 1200 seconds; revived timed-out tasks restart the clock at their next actual execution.
- Real Worker/Planner/main Agent execution status consistently drives task badges, fixing Idle while workers run.
- Worker conversations retain individual controls and current-conversation token totals; model names move to the right of the conversation title.

#### Removed

- Removed worker multi-select/select-all/bulk-pause/resume UI and the corresponding bulk-control API/mock contracts.
- Removed worker row token badges/tooltips; the complete underlying token ledger and task aggregate APIs remain.

### LLM

#### Added

- Tasks support ordered LLM profile chains. Explicit provider quota exhaustion automatically switches to the next profile and persists current profile, exhaustion state, and error summary.
- Running/paused tasks can edit the complete chain, reorder profiles, and manually switch the current profile, effective at the next LLM call.
- Automatic/manual switches and full-chain exhaustion generate structured system activity and deduplicated task-activity notifications.
- Added a task-role model-resolution API covering the next Main Agent, Planner, and Worker calls.
- Added an optional global LLM Pool with configured order, fallback for bound-model failure, health status, cooldown recovery, and manual reset.
- Added an always-on LLM usage ledger aggregating input/output/cache tokens by task, conversation, model, and profile.

#### Changed

- Goal decomposition, Planner, Worker, and main Agent share the task-level LLM runtime; resolution priority is described below.
- Failover responds only to explicit quota/balance/billing errors; ordinary rate-limit/authentication/network/server/context errors do not incorrectly switch profiles.
- LLM configuration now uses cards and a right-hand editing drawer, with Pool order, priority, exclusions, health, and recovery operations.
- Exhausted-chain information wraps on narrow screens. Current-model identification moves from conversation-list icons to a label beside the conversation title with a full tooltip.
- Role-level LLM priority is Agent binding → task chain → global: explicitly bound roles always use that model; unbound roles use the task chain, or global settings when the chain is empty.
- Empty outbound-proxy configuration means direct connection, no longer falling back to `HTTP_PROXY` / `HTTPS_PROXY`; explicit `ARTEX_LLM_PROXY` remains effective. Proxy inputs accept `socks5://user:pass@host:port`.

#### Fixed

- Transient streaming failures before any output are safely retried with backoff on the same provider, reducing runs that stop after one or two tools without a summary and end as `model_error`. Quota exhaustion, excessive context, and deterministic 4xx errors are not retried; failover/compaction recovery handle them respectively.

#### Removed

- Removed conversation-list LLM icons to avoid confusion with Worker status icons.

### UI

#### Added

- Global task categories support creation, renaming, deletion, and filtering; new tasks can select a category directly.
- Added global task-template CRUD and a right-side management drawer. New tasks can load preset descriptions/goals or save current contents as a template.
- New tasks can link multiple source tasks and enterprise scopes. A single multiline scope input recognizes domains, URLs, IPs, CIDRs, ICP registrations, and enterprise keywords.
- Task test assets can be added/deleted live with origins such as human, enterprise, inheritance, or Agent discovery. Worker conversation titles show current test assets and origin summaries.
- Findings group by task with independent group pagination; descriptions can initiate high-priority deeper-investigation intents.
- Conversations support rename/pin/unpin/delete; task lists support current-page bulk pause/resume.
- Traffic and tool execution details use right-hand drawers. HTTP messages include `Host` and highlight request lines, status codes, headers, JSON, and markup bodies.
- Task actions add Details and Pause/Resume buttons. Conversation send shortcuts are configurable (`Enter`, `Cmd+Enter`, etc.); web-search settings add a proxy input.
- Conversation-title badges show LLM profile names, moving model IDs to hover tooltips. Tasks gain optional names and a searchable name column, falling back to descriptions when empty.
- Skills show usage counts, last-used time, and missing-dependency statistics to identify inactive/uninstalled skills.

#### Changed

- Task titles become focusable detail links. Task assets, enterprise assets, finding task groups, and grouped findings use real server pagination, stable sorting, and exact totals.
- Enterprise creation uses a right-hand drawer with unified multiline scope input, live recognition, validation, and type preview.
- Main Agent input grows automatically, sends with Enter, inserts newlines with Shift+Enter, and avoids accidental submission during Chinese IME composition.
- Agent previews, task reports, and related details share Markdown rendering. Standardized fixes for deletion confirmations, long errors, and mobile drawer widths.
- Template selection displays/searches names and submits IDs. Restored original system font sizes; version displays consistently show `0.3.3`.
- Task/current-conversation token totals highlight only input/cache-read/output values; send uses a simpler upward-arrow icon.
- Task overview scope displays enterprise names rather than IDs.

#### Fixed

- Ordinary text containing dots is no longer misclassified as an ICP registration number.
- Fixed duplicate variable-editor keys when directory variables collide with global runtime names such as `{{.Now}}`.

#### Removed

- Removed separate Enter buttons from task cards; task titles open details.
- Removed dashboard New task button, enterprise Logo URL field, and finding-count badges at the findings-page top and within task groups.
- Reverted the global 10% font enlargement and removed worker checkboxes, model icons, and row token statistics from conversation lists.

### Agent

#### Added

- New tasks can link multiple existing tasks and inherit direct sources' goals, facts, findings, completed intents, scope, and blackboard context live and read-only.
- Blackboard tools can retrieve source-task nodes/facts/findings/execution traces on demand. Inherited nodes carry source labels and all write tools reject modifications.
- Enterprise-scope tools accept domains, URLs, IPs, CIDRs, ICP registrations, and enterprise keywords. Keywords guide Agent scope only and do not determine automatic asset ownership.
- Deeper investigation creates a high-priority human-origin Worker intent in the original task with asset anchors and a `derived_from` edge.
- Overview adds Goal management with view/add/edit/delete. Add/edit notify Planner and revive the task; delete hard-deletes the goal and cascades edges/anchors without revival.
- Main Agent defaults to `steer_work`, injecting correction instructions into running workers without interruption or progress loss while checking intent ownership against the task.

#### Changed

- Source-task intents never enter the new task's frontier; each new task keeps independent exploration, execution queue, working directory, and conversation history.
- Deleting a running intent no longer destroys data: stop as `stopped`, require a reason, attach it as a fact and write it into payload, then notify Planner through `cancelled`, preserving intent contents/reason.
- Main Agent conversations use server activity streams. Failed sends restore input; opening scrolls to the bottom and keeps the final reply visible after lazy detail loading.
- Task pause terminates current Main Agent/Planner/Worker calls but does not prevent users starting new main Agent orchestration conversations while paused.
- Cancellation, shutdown, and stream interruptions preserve actual termination reason, generated content, run rounds, duration, tokens, and unanswered tool calls.

#### Removed

- Removed optimistic main Agent message echo to prevent duplicates and cross-conversation streams under pause/failure/concurrent activity.

### Constraints

#### Added

- Goal decomposition first extracts allow/deny operation constraints, then goals; main Agent can add constraints during execution. Constraints have highest priority in Planner/Worker system prompts, explicitly governing debiased diversity and broader exploration.
- Overview adds Constraint management and CRUD APIs. Injection can be enabled separately for Planner/Worker, both on by default, reread every round and effective immediately. `task_constraints` uses `CREATE TABLE IF NOT EXISTS`, automatically adding it to old databases.

### Interception

#### Added

- Added model-fallback command approval beyond regex/string rules. If no rule matches, the model judges `ALLOW` / `ASK` / `DENY`; failure fallback and approval-timeout actions are configurable. The interception page separates Rules and Model configuration tabs; model decisions show a Model prefix and reason.

#### Fixed

- Hardened judge-output parsing so a correct `DENY` cannot be mistaken for permission.

### Traffic

#### Changed

- Records complete exchanges in SQLite, spilling large bodies into hash-deduplicated blob storage. Text bodies use trigram full-text indexing for arbitrary substrings and Chinese searches. Deletion becomes one SQL transaction, dropping from hours to milliseconds without stopping recording. Added streamed large-body downloads and `traffic_search` full-text parameters. Legacy file-tree data needs no migration and remains readable/searchable/deletable.

### Tasks and assets

#### Added

- New tasks offer an Asset coverage switch, on by default. Disabling it removes coverage calculation/display, shows only assets in the situation graph, stops automatic `task_scope` accumulation, and removes related tools from Planner/main Agent. Enterprise association is unaffected.
- `insert_assets` accepts per-asset `related`, default `true`, effective only with coverage enabled. `false` stores the asset globally but excludes it from this task's coverage, e.g. incidentally discovered neighboring/unrelated assets.
- Enterprise scope/task test assets share text recognition for domains, URLs, IPs, CIDRs, ICP registrations, and keywords. Domains/IPs create/reuse global assets; CIDR/ICP/keywords remain task-scope context.

### Build

- `build.sh --release` builds Linux amd64/arm64, macOS amd64/arm64, and Windows amd64 together, generating ZIP releases with `skills/`, sample configuration, and README.
- Go linker strips debug information for ZIP releases. UPX becomes explicitly optional to avoid segmentation faults from self-extracting ELF executables on some Linux systems.
- Release workflows smoke-test Linux amd64 startup and include `SHA256SUMS` with archives.

### Contributors

- [@neouks](https://github.com/neouks)

## [0.3.2] - 2026-08-20

### Added

- New tasks can link multiple existing tasks, inheriting direct sources' facts/findings/completed intents/scope/blackboard context live and read-only while retaining independent queues, working directories, and conversation histories.
- Tasks support ordered LLM chains. Explicit provider quota exhaustion switches to the next profile and persists current profile, exhaustion state, and structured audit activity.
- Running/paused tasks can edit/reorder chains and manually switch profiles; automatic/manual switches and full-chain exhaustion appear in task details.
- Running workers support pause/resume/cancel. Pause preserves blackboard data; cancellation transactionally removes the intent and directly produced facts/findings/execution records after the worker stops writing.
- Task deletion can also clean associated assets, traffic, findings, and task files, with deletion barriers, concurrency protection, and auditable deletion statistics.
- Added optional task concurrency limits. New tasks queue FIFO at capacity and start automatically when slots free.
- Added task-asset and company-asset pagination, task-category statistics, and paginated intent mock contracts.
- Added `build.sh` single-binary builds with static frontend export, embedded resources, cross-platform targets, and injected version information.

### Changed

- Explicit task-level chains coexist with the global LLM Pool. Explicit chains retain strict quota-failover semantics; without a chain, Agent bindings/global rules remain in effect.
- Main Agent input supports growing multiline text, Enter to send, Shift+Enter for newlines, and protection against Chinese IME composition submissions.
- Planner/Worker/main Agent share task-level LLM runtime. Task chains use the smallest candidate context window as the safe compaction threshold.
- Task details show running/idle/paused according to actual LLM calls. Source facts/findings/intents/asset references/graph nodes consistently show origins and remain read-only.
- Agent previews, task reports, and related details use shared Markdown rendering.
- LLM profiles use cards/drawers, with independently scrolling model lists inside drawers.
- Task pause no longer blocks main Agent conversations: orchestration is independent and users may send new messages while paused; pause only ends the current round.
- Worker default run duration increased from 600 to 1200 seconds.

### Fixed

- Removed optimistic main Agent console echo in favor of server-only rendering, fixing scrambled messages and cross-conversation content after pause/send failures.
- Opening a main Agent conversation scrolls to the bottom and shows the final reply fully; lazy-loading its full contents scrolls again instead of pushing it off-screen.
- Fixed main Agent conversations continuing after task pause and orchestration-Agent pauses failing to stop main Agent synchronously.
- Fixed task badges/actions becoming inconsistent on task completion or while Planner/Worker/main Agent actually runs.
- Fixed late blackboard writes/residual files racing worker cancellation, task deletion, and concurrent writes.
- Fixed broken Markdown previews, overflowing deletion-confirmation text, mobile widths, and misaligned task-detail buttons.
- All Agent cancellation paths now have named termination reasons. Activity details show the canceling actor, terminal state, rounds, duration, token usage, and unanswered tool calls.
- Fixed parent-context cancellation racing and overwriting named `shutdown` reasons during backend shutdown, and byte-truncated Chinese activity summaries producing invalid text.
- Fixed cancellation events with partial streaming output losing their actual termination reason; pre-cancellation generated content is retained.

### Contributors

- [@Autumn-27](https://github.com/Autumn-27)
- [@neouks](https://github.com/neouks)

[Unreleased]: https://github.com/Autumn-27/ARTEX/compare/v0.3.10...HEAD
[0.3.10]: https://github.com/Autumn-27/ARTEX/compare/v0.3.9...v0.3.10
[0.3.9]: https://github.com/Autumn-27/ARTEX/compare/v0.3.8...v0.3.9
[0.3.8]: https://github.com/Autumn-27/ARTEX/compare/v0.3.7...v0.3.8
[0.3.7]: https://github.com/Autumn-27/ARTEX/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/Autumn-27/ARTEX/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/Autumn-27/ARTEX/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/Autumn-27/ARTEX/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/Autumn-27/ARTEX/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/Autumn-27/ARTEX/compare/v0.3.1...v0.3.2
