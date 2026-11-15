# Long side conversations and context budgets

Fixed on 2026-09-11. The original implementation treated request-JSON character counts directly as tokens and inherited the main task's 32K output reservation. It therefore rejected valid side questions prematurely when the context contained substantial HTML, JavaScript, or tool output.

## Review of open-source implementations

- [Grok CLI side context](https://github.com/superagent-ai/grok-cli/blob/fb97af83f06dca873281d60168430f06c8de6324/src/agent/agent.ts#L739): extracts recent user/assistant text, with a character budget of about 2000 and at most 400 characters per message. This path does not maintain a continuous side-conversation history.
- [Grok CLI independent requests](https://github.com/superagent-ai/grok-cli/blob/fb97af83f06dca873281d60168430f06c8de6324/src/utils/side-question.ts): independent cancellation signal, an output cap of 2048 tokens when the model supports it, and no tools.
- [Grok CLI main-session compaction](https://github.com/superagent-ai/grok-cli/blob/fb97af83f06dca873281d60168430f06c8de6324/src/agent/compaction.ts): estimates tokens, retains recent content, updates the previous summary with new content, and handles truncation across turns.
- [OpenCode session compaction](https://github.com/anomalyco/opencode/blob/b3f1a96c6dd7adeb28b36dd11add1998fc84d67b/packages/core/src/session/compaction.ts): whole-request estimation, output/buffer reservations, recent content plus rolling summaries, and tool-free summary requests. Its default recent-content budget is 8000, with summary output capped at 4096 tokens.
- [OpenCode overflow recovery](https://github.com/anomalyco/opencode/blob/b3f1a96c6dd7adeb28b36dd11add1998fc84d67b/packages/core/src/session/runner/llm.ts): attempts recovery only before assistant output starts; the recovered call cannot reenter the same overflow-recovery path.

ARTEX adopts independent output budgets, recent content plus rolling summaries, and bounded recovery. It retains norma v0.3.6 structured messages and tool pairing rather than copying Grok's text excerpts, and does not write OpenCode-style main-session compaction events into ARTEX's main transcript.

## Request budgeting and execution

- Messages use norma's content-block UTF-8 byte estimate with a 4/3 margin, plus system prompts, tool schemas, and message-framing overhead. This is not the model's exact token count.
- Side output defaults to at most 8192 tokens and cannot exceed the output cap already configured for the main model. The service environment variable `ARTEX_BTW_MAX_OUTPUT_TOKENS` sets a cap from 256–32768; it does not change the product's default model or main-task parameters.
- The input budget is the context window minus the output cap and safety margin. Unknown windows use the platform default of 200K. The safety margin is 5% of the window, at least 128 and at most 8192 tokens.
- Successful exchanges are loaded in increasing ordinal order, at most 20 per batch. At most 20 exchanges remain verbatim, using no more than one quarter of the input budget or 16K tokens, whichever is smaller.
- Excess exchanges update the rolling summary. Summaries carry historical provenance and context time. Historical assistant answers are not new tool evidence; the latest main snapshot takes precedence in a conflict.
- If the main context is still too long, only old messages in its copy are summarized, retaining at most 8K recent tokens. Cuts do not split tool-call/result pairs. An oversized group is summarized as a whole.
- Summary inputs are split at UTF-8-safe boundaries according to the actual remaining window, with output capped at 2048 tokens. Empty, truncated, tool-producing, or over-budget summaries are not cached. A side request makes at most 12 summary calls under the same 120-second timeout; reaching the limit fails explicitly rather than looping indefinitely.
- If the first model attempt reports context overflow before any text or tool call is emitted, further reduction permits at most one retry. Recovery stops immediately if the estimate does not shrink. Other model errors and partially streamed responses do not trigger this recovery.
- All received usage, including summaries, failed attempts, and cancellation-time usage, accumulates into the same side request. When a Provider supplies no usage, only zero can be recorded; estimates must not be presented as actual usage.

## Persistence and interface

`side_question_sessions.memory` stores the older-exchange summary, its covered ordinal, and main-context summaries cached by snapshot identity. `side_question_requests.context_info` stores the preparation phase, actual replay count, summary usage, and budget estimates.

Summaries are saved only while the original request is running and its clear generation still matches. Clearing also removes caches; late writes cannot restore cleared data. New snapshots do not reuse old snapshot summaries. Summary fields are included in v3 task archives; missing fields in older v3 archives are restored as empty objects, while v1/v2 compatibility remains.

POST accepts and returns the request first; preparation and compaction run in the background without holding the admission lock or a database transaction. SSE/history display preparation, exchange consolidation, copy compaction, and answering phases. Compaction failures are persisted as terminal failures of that side request. The frontend retains the error and restores the failed question's draft without a floating notification obscuring the input; history polling no longer clears submission errors.

## Validation record

- Replay of 19/20/21/50 exchanges, retention of older conclusions beyond 20 exchanges, and summary-cache reuse after restart: automated checks passed.
- Very long Chinese answers and code context, chunked-request budgets, tool pairing, immutable snapshots, and cache invalidation for new snapshots: automated checks passed.
- Summary failure/cancellation/truncation/oversize/tool responses, clear races, call limits, single overflow recovery, and no retry after partial output: automated checks passed.
- Independent PostgreSQL pagination, restart, v1/v2/v3 archives, summary-cache and budget-metadata archive restoration, old v3 archives missing new fields, and 20 parent sessions sharing four concurrency slots: passed.
- Go candidate-service build, frontend TypeScript checks, Biome checks for changed components, and a Next.js production build in an independent directory: passed.
- The built-in browser used an independent UI fixture at 1280×720 and 390×844 to verify consolidation phases, summary-scope hints, draft restoration after failure, absence of floating error notifications and horizontal overflow, and an error-free console. The temporary fixture was removed.
- Read-only replay of existing local Worker snapshots passed the new budget checks. For example, Worker #3's 293085-character snapshot was no longer incorrectly rejected by local character counting. This involved no external model call.
- A real conversation test sending a private Worker snapshot to Grok was rejected by automated approval review. It was not executed and is not counted as passed.
- On 2026-09-11 at 00:37, the local backend was restarted at the user's request using the existing database, data directory, and login configuration. The running file matched the candidate binary's SHA-256; both backend and frontend-proxy `/api/health` endpoints returned healthy responses.

Validation commands (independent test database only):

```sh
go test -race ./sidequestion ./db ./server -run 'TestSide|TestCheckpoint|TestSnapshot|TestBuildRequest|TestService|TestMainSide|TestTaskArchive' -count=1
go build ./cmd/artex
npx tsc --noEmit
npm run build -- --webpack
```

The frontend production build used an independent copy to avoid overwriting the current preview's `.next`. The candidate service was at `/private/tmp/artex-btw-budget-candidate`, copied to `/private/tmp/artex-btw-preview/artex` and started; the original binary was backed up as `artex.before-context-budget` in the same directory.
