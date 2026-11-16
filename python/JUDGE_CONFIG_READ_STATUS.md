# Standalone resolved judge configuration read

New db/judge_config_read.py and server/judge_config_read.py supply an unregistered
GET `/api/intercept/judge` adapter. Seven authored and five independent tests pass. PostgreSQL stage 111 passed
five actual database groups and twelve focused tests; see ISOLATED_QA.md. No writes, model/provider calls, matcher
activation, profile resolution or execution approval occur.

Source: server/intercept.go interceptGetJudgeConfig; intercept/intercept.go
judgeConfig/getSettingInt/getSettingChoice; db/settings.go GetBool. Exactly seven
llm_judge_* settings are selected. Missing values resolve to enabled=false,
profile_id=0, timeout_seconds=15, fail_action=allow, ask_timeout_seconds=300,
ask_timeout_action=deny and the original built-in prompt. These source defaults
are metadata, not a policy permission or a recommendation to fail open. Exact
true/1 enable; other bool strings do not. Signed 64-bit integers use Go whitespace
and parse behavior; nonpositive timeout values default, negative profile IDs are
retained. Choices are trimmed and case-sensitive. Nonblank saved prompts retain
all original whitespace; blank uses the built-in default.

The English default is copied as inert data from translated intercept/prompt.go
DefaultJudgePrompt plus JudgeOutputContract. Required escaped legacy parser labels
are retained. GET does not append the runtime JudgeContextBoundary. A source-only
literal extraction test checks exact parity without executing Go. UTF-8 SHA-256:
`5bc02583a3e6b343c1fb89def2286b708793f0f40c5fa89ad164ffc10ca761a6`.

Single-snapshot SQL caps cumulative selected text before transfer; oversized,
malformed or unexpected rows fail redacted. Only profile ID is disclosed, never
profile keys/URLs or unrelated settings. Global configuration read and separate
raw-prompt grants, current policy, original callbacks/executor/bounds are fenced
before SQL and after serialization/guard exit. Eventual routing still requires
original JWT authentication. Read-only/cooperative lifetime policies are assumed;
finite repeated checks are not an arbitrary-callback fixed point.

Intentional hardening: SQL/storage failures return a redacted error rather than
source's swallowed errors/defaults. Missing keys in a healthy store still produce
source defaults. Prompt/text overflow refuses instead of truncating or silently
selecting a different policy. Query/body/other methods are rejected. No service
registration or automatic default activation is provided here.

## Optional metadata composition (separate acceptance)

`bind_metadata_reads(..., judge_config_service=...)` explicitly mounts the protected
GET `/api/intercept/judge` only when the exact service/reader is provided. No default
judge, model or provider is created. The new `judge_config` global grant remains
separate from the service's configuration/raw-prompt/current grants. Original JWT
and all disclosure grants are rechecked after serialization and start_response;
late denial aborts body transmission. PUT and other mutations are not installed.

Existing fixed-path conflict checking rejects intersecting original GET/HEAD
routes before mutation. Exact original WSGI/AuthService and common executor rules
remain unchanged. The route inventory accepts only the exact audited
`judge_config in services` guarded GET declaration in the metadata wrapper.
Unknown paths/guards/files/methods remain refused; the inventory is generated from
the reviewed frozen candidate.

Three new composition and two scanner tests pass; 38 selected surrounding tests
pass together. Four independent review tests pass. PostgreSQL stage 112 passed
four actual database/JWT groups and 43 focused executions, separate from the
accepted standalone reader's PostgreSQL stage 111.
