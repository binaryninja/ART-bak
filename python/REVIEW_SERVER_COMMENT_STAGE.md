# Server retry/deadline comment translation

Comments translated in `server/llmretry.go`, `server/engine_timeout.go`, and the
queue-capacity comment in `server/scheduler.go`. No executable source or string
literal was intentionally changed. The existing isolated-QA Go toolchain's
`gofmt` formatted all three files. A lexical comparison against pre-edit copies,
excluding only comments and whitespace while preserving literals and executable
tokens, passed for each file after formatting. No upstream program was executed.

## Deliberately retained exceptions

- The two comment references to `docs/LLM重试设计.md` and
  `docs/任务级超时与收尾设计.md` preserve existing documentation filenames.
- Scheduler prompt fragments, event labels, and truncation text remain unchanged
  runtime literals (13 lines containing CJK).
- Engine-timeout log messages and the final-assessment summary remain unchanged
  runtime literals (10 lines containing CJK).
- Other server files and all tests are outside this bounded translation pass.

These translations describe upstream Go behavior. They do not enable timed
Python execution: the prepared dispatch-clock helper is not an expiry
coordinator, and nonzero task timeouts remain unsupported by the Python engine.
