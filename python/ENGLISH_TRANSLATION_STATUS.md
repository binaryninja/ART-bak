# English translation status

The Python implementation is English. Original source remains as a compatibility
reference; untranslated runtime literals and historical documents still exist.
This is not a claim that every Chinese character in the repository is removed.

The latest comments-only pass translated 438 Chinese comment lines across 17
production agent Go files. Other inspected files already had English comments.
All production agent comment prose is now English. Actual documentation path
references retain their target spelling rather than inventing broken translations.

Saved originals were compared with the formatted outputs using a lexer that
preserves and compares every executable token and quoted/raw/character literal.
These matched exactly; official gofmt and whitespace checks passed. No original
Go execution was needed for comment-only transformations. Runtime prompt/log/error
literals remain separately tracked work, not silently altered compatibility data.

Earlier UI, prompt, documentation, helper and schema translations have separate
regression coverage. The reference schema retains legacy persisted values with
Unicode escapes; escaping is compatibility preservation, not prose translation.
Use tools/translation_inventory.py for an audit, not automatic replacement.

## Bounded server presentation translations

The following source error text is now English. JSON keys, routes, IDs, persisted
values, status codes, formatting placeholders and control flow were preserved.
These are deliberate presentation changes, not claims of byte-identical errors:

- `server/workspace.go`: 18 error-message occurrences (10 distinct messages),
  plus one directory-ordering comment. No literal Han remains in this file.
- `server/task_categories.go`: seven error-message occurrences.
- `server/task_metadata.go`: four error-message occurrences.
- `server/task_templates.go`: six error-message occurrences.
- `server/task_categories_test.go`: the one existing substring assertion for the
  task ID batch-count error now expects its English counterpart. No other test
  fixtures or assertions were changed in this pass.

The three task production files contain no remaining literal Han. Exact-string
consumer searches found the category test assertion above; no matching frontend
error-text logic was found. Formatting verbs (`%s`, `%d`), limits and field labels
remain intact. This does not assert that external consumers never inspect errors.

Validation: original snapshots plus explicit translation maps were compared with
formatted output using the authored Go lexer. Every executable token and every
literal matches after applying only the allowlisted presentation translations;
workspace's one comment is excluded from executable-token comparison. Official
`gofmt`, whitespace checks and original 0644 file modes pass. No original Go code
was executed for this pass; Go runtime tests remain the isolated QA step.

Untouched exceptions: other server runtime prose, model prompt literals, legacy
wire/persisted values, unrelated test fixtures, third-party code, licenses and
historical evidence remain outside this bounded pass. In particular, unrelated
skill-upload localized-error assertions were not rewritten. Documentation link
paths retain their actual target spelling. This is not whole-repository English
completion, nor a claim that escaping a compatibility literal translates it.

### Worker intervention presentation and additional comments

`server/intent_intervention.go` now has its 11 user-facing error messages in
English, with no literal Han remaining. Worker/task lifecycle restrictions,
request ID character/length rules, HTTP codes and field names are unchanged.
Exact-string searches found no consumers in server tests or the frontend; no
test fixture needed modification. The explicit translation map, saved original
and post-gofmt lexer comparison prove every other executable token/literal is
unchanged. File mode remains 0644. No original Go execution was performed.

A separate comments-only worker completed these seven production files:
`server/assembly.go`, `server/llmpool.go`, `server/constraints_api.go`,
`server/goals_api.go`, `server/task_control.go`, `server/task_intercept.go` and
`server/task_archive_package.go`. The worker reports exact token/literal equality
against saved originals after official gofmt, with hashes and proof retained in
the shared QA evidence. Existing interactive-shell and LLM-rotation documentation
links retain their actual Chinese filename targets. Runtime strings in that
comments-only slice were not translated. `mcpdiscover.go` and `logsink.go` were
inspected but unchanged. These bounded passes do not establish whole-server
English completion.

### Asset DSL and constraint error presentation

Translated 11 error-message occurrences in `db/asset_dsl.go` and four occurrences
(two distinct messages) in `db/constraints.go`. These cover syntax, invalid field
or operator, integer requirements, invalid constraint kind, and missing constraint
errors. Repository consumer/test searches found no comparisons against these
localized messages; the constraint HTTP layer forwards store errors as prose.
Its separate validation messages were not changed in this tranche.

An explicit 13-string mapping, original copies and before/after hashes were saved
in the review evidence. Exact lexical comparison after official gofmt verified
that every executable token and literal outside that mapping is unchanged,
including format verbs, SQL, aliases, kind values and control flow. Both files now
have zero literal Han. No original Go code was executed and no test assertion
needed alteration. `db/task_scope.go`, `db/company_scope.go` and schema SQL were
not edited; the `备案` input matcher remains unchanged. This is not a claim of
whole-repository English completion.

### Goal/constraint HTTP presentation follow-up

A fresh scan found no literal Han in `web/src` TypeScript/TSX files. The bounded
fallback translated nine error occurrences in `server/goals_api.go` and seven in
`server/constraints_api.go` (12 distinct messages). Repository searches found no
exact localized error-string consumers or test expectations needing adjustment.
These files now have zero literal Han. Status codes, JSON keys, policies, query
arguments, SQL and all nonmapped executable tokens/literals remain exact by
explicit mapping and lexical comparison after official gofmt. Saved original
copies and hash evidence accompany the mapping. No original Go code was run;
intercept/CAS flows and the separate database exploration errors were untouched.
