# `/btw` validation record

Date: 2026-09-10. Branch: `codex/btw-side-question`. Baseline: `8dae851b9b622f2ff2631f332fde9719d0b16fba`.

Used an independent PostgreSQL test database and data directory. Real model credentials were injected only into the isolated test environment, never written into the code or this record, and the product's default model was unchanged. Go 1.26.3, norma v0.3.6, Next.js 16.2.9.

Actual model conversations, returned objects, engineering assertions, and Qwen's original review text are stored in [validation-2026-09-10.json](validation-2026-09-10.json), which contains no API credentials.

## Engineering checks

| Scope | Result | Evidence |
| --- | --- | --- |
| Deep copying of structured messages and tool arguments | Passed | `TestCheckpointDeepCopyAndBoundaries` |
| Summary/compaction requests do not overwrite snapshots; complete responses and terminal states publish; partial responses excluded | Passed | `TestCheckpointDeepCopyAndBoundaries`, `TestSnapshotExcludesPartialStreamAndSelectsPoolMember` |
| Actual selected model-pool member identity | Passed | `TestSnapshotExcludesPartialStreamAndSelectsPoolMember` |
| Tool pairing, 20-exchange replay, budget trimming, and overflow errors | Passed | `TestBuildRequestCompactionToolPairingAndBudget` |
| Main/side concurrency and cancellation isolation in both directions | Passed | Blocking Provider, `TestMainSideConcurrencyAndIndependentCancellation` |
| No tool execution, streaming/non-streaming, and usage retained on failure | Passed | `TestServiceNoToolsAndUsageOnFailure` |
| Actual norma ChatAgent + local Read tool; main transcript/activity isolation | Passed | `TestSideActualChatCheckpointToolResultAndTranscriptIsolation`, streaming and non-streaming subcases |
| Persistence, pagination, idempotency, and partial-answer retention on restart | Passed | `TestSideHistoryIdempotencyPagingAndRecovery` |
| Clear/late-write races, parent deletion, and version comparison | Passed | `TestSideClearLateWritersAndDeletedParent` |
| MainAgent/Worker archive and restore, v1/v2/v3 | Passed | `TestSideTaskArchiveVersions` |
| Three parent interfaces, authentication, resource ownership, and Worker logical deletion | Passed | `TestSideHTTPGlobalLimitTaskWorkerAndDeletion`, `TestSideCheckpointPersistsBeforeAdmissionAndRestart` |
| Side questions during a busy main session; independent SSE reconnect/disconnect, cancellation, and clear | Passed | `TestSideHTTPBusyIsolationClearAndReconnect` |
| One request per parent / four globally | Passed | Two `TestSideHTTP…` cases |
| Snapshot persisted before submission, follow-up after restart, and no fabricated snapshots for old sessions | Passed | `TestSideCheckpointPersistsBeforeAdmissionAndRestart` |
| Further requests rejected after cached configuration deletion or model change | Passed | `TestSideRejectsDeletedOrChangedCachedProfile` |
| Cancellation and final answer/usage persistence completed before archiving | Passed | `TestSideTaskDrainPersistsBeforeArchive` |
| Usage recorded once with side-request attribution when the streaming consumer cancels early | Passed | `TestSideUsageRecordedOnceOnConsumerCancellation` |
| Automatically restored Worker/deadline runtime contexts continue publishing snapshots after restart | Passed | `TestSideRestoredWorkerRuntimePublishesNewCheckpoint` |
| Race checks for relevant packages | Passed | Commands below |
| TypeScript and production build | Passed | `npx tsc --noEmit`, `npm run build` |
| Biome checks for new frontend modules | Passed | `biome check`, three new modules |

To reproduce the automated checks, configure `ARTEX_PG_DSN` for a separate disposable database (never point it at production):

```sh
go test -race ./agent ./db ./server ./sidequestion ./llmrec ./llmpool \
  -run 'Test(Side|Checkpoint|Snapshot|BuildRequest|Service|MainSide|CaptureRun|TaskArchive|CompleteForwards|StopIntent|CancelIntent)' -count=1
cd web
npx tsc --noEmit
npx biome check src/lib/side-questions.ts src/hooks/use-side-questions.ts src/components/side-question-workspace.tsx
npm run build
```

The full Go regression suite was not entirely green: two existing tests in `server` failed during temporary-directory cleanup, both with `TempDir RemoveAll … directory not empty`:

- `TestInheritedActivityDetailAndRelationDeletion`
- `TestTaskMetadataPatchReturnsRenameAndPin`

Exporting source from the unchanged baseline above and rerunning `server` in the same isolated environment reproduced both cleanup failures. The baseline run additionally failed a target-node-count assertion in `TestCoreTaskLifecyclePG`; the final modified `server` regression did not have that assertion failure. Other packages passed, as did the side-question-related cases and race checks. Baseline problems were not marked as accepted for this change, and existing assertions were not modified to hide problems.

The Next.js build emitted existing multiple-lockfile / workspace-root inference warnings. The build completed and all pages were generated successfully.

## Browser checks

Used the Codex In-app Browser connected to an independent local Go service and Next.js development server. The following interactive automated operations were completed on desktop and a 390 × 844 narrow viewport, with screenshots and browser logs inspected:

- Entered `/btw` during regular chat execution; main and side content appeared concurrently, and the desktop sidebar worked.
- Asked consecutive follow-up questions; stopping the side request retained its partial answer while the main flow continued.
- Requests continued after closing the panel; reopening restored the completed answer. After refreshing, an empty `/btw` restored history.
- The narrow-screen Drawer's input, buttons, history, and close actions worked without horizontal overflow.
- Clearing used a confirmation dialog; afterward the history disappeared while the main transcript and snapshot remained.
- Asked separate questions in task MainAgent and two Workers, then switched between them; Agent labels and histories did not cross sessions.
- A blocking local model fixture kept a Worker running. Submitting `/btw` from the Worker's main input and stopping the side request left the Worker showing live execution and its own pause button; the side request retained its partial answer.
- Browser error/warning logs were empty.

Controlled fixtures verified concurrency timing precisely without relying on real-model response speed. Two initial Worker checks during debugging failed to establish a valid overlap window (the task had already ended / the answer ended early). After correcting the fixture, these checks were repeated and passed; the initial attempts were not counted as valid passes.

## Actual model conversations

First probed `grok-4.6` through the OpenAI-compatible endpoint `http://127.0.0.1:12580/tingly/openai`. The probe returned HTTP 200, model name `grok-4.6`, and `READY` in 2.82 seconds. Since the preferred model was available, the Tingly `glm` or Zhipu `glm-5.3` fallback chain was not activated; neither fallback service was verified in this run.

| Scenario | Actual result |
| --- | --- |
| Asked about assets, goals, and markers while the main session ran | Returned `redhaze.top`, the homepage-reading and summarization goal, and `BTW-REAL-0910`; side request completed in 16.97 seconds |
| Asked about tool evidence after the main session finished reading the homepage | Correctly cited WebFetch 200, curl redirects 301 → 302 → 200, and the page title; 7.24 seconds |
| Side request asked Bash to create a test file | Refused execution; the target file was not created; 7.74 seconds |
| Completed side request did not alter main context | Main transcript SHA-256 and main activity records remained unchanged; side tool-execution count was 0 |
| Follow-up after actually stopping/restarting the Go service | Retained the previous three side-history entries and answered assets, markers, and title directly from the persisted snapshot, without rerunning the main Agent |
| New session using Grok non-streaming configuration | Correctly answered the asset and `ATOMIC-0910`; returned and saved usage: input 11734, output 138, cache_read 11520 |

The asset example's main session used WebFetch and Bash/curl to read a public homepage. The landing page was `https://id.redhaze.top/home`, with a Chinese-language title translated here as “RedHaze Technology, RedHaze Group · Global Integrated Group Portal.” The exact observed title, with Han characters represented as Unicode escapes, was `\u7ea2\u5e55\u79d1\u6280 RedHaze Group · \u5168\u7403\u7efc\u5408\u96c6\u56e2\u95e8\u6237`. Bash temporarily stored the response in a local test file; no remote writes were performed. This fact was checked separately from the statement that the side request executed no tools.

Main transcript checksum: `e7e61f135a4a120954b539f357e8c4205d7d5cd7460dcaf3dc0fd066463e1d00`.

**Usage limitation:** Tingly's Grok streaming responses supplied no usage. A separate direct request with `stream_options.include_usage=true` confirmed HTTP 200, 12 data frames, and 0 usage frames. Thus zero in the streaming tests means the endpoint provided no usage, not that no billing occurred. Non-streaming usage and the fixtures' failure/cancellation usage were saved correctly.

## Qwen review

The review model was `qwen-flash`, using the OpenAI-compatible endpoint `https://dashscope.aliyuncs.com/compatible-mode/v1`, which returned HTTP 200. It received the first three actual side conversations, main-session tool evidence, and engineering assertions. It returned `verdict: accept`, `concerns: []`, finding the answers consistent with the assets, markers, and page-reading evidence, and the refusal to execute side tools consistent with the constraints. Review usage: prompt 6625, completion 312, total 6937.

The Qwen review did not cover the subsequently added service-restart and non-streaming tests. Its summary of “no writes” was too broad: the main session's curl did create a local temporary response file, as explicitly recorded above. Concurrency, zero side-tool execution, and transcript isolation are assessed by engineering assertions; model review only supplements answer-quality evaluation.
