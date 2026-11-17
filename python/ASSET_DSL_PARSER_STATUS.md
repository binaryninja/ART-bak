# Asset DSL lexical compatibility

`artex/asset_dsl.py` implements the checked-in `db/asset_dsl.go` lexer, expression
parser and bound-parameter WHERE compiler. This note covers a narrow lexical
correction; it does not add query execution or change task-scope authority.

Go's `tokenize` advances one byte when `readToken` returns an empty bare word.
Python previously advanced past both quotes in `""`, which could turn adjacent
text into a field clause or lose a closing quote. The port now preserves the
upstream cursor rule:

| Input | Result |
| --- | --- |
| `""port=80` | One bare-text value `port=80`, bound as `%port=80%` |
| `""example"` | One bare-text value `example`, without a trailing quote |
| `""port=80 OR port=443` | One bare-text value containing the operator text |
| `""AND"` | An incomplete operator expression, as with bare `AND` |
| `domain=""` | An empty field value, still bound as `%%` for `ILIKE` |

This intentionally retains unusual upstream behavior. Unterminated quotes remain
accepted; backslash is not a quote escape; quoted bare `AND`/`OR` remain operators.
Unknown field-like tokens remain full-text searches. Array `!=` excludes exact
membership, and task/company ID operators still compile to equality regardless
of the parsed operator. No field allowlists or SQL templates changed.

Documented Python differences remain: Unicode whitespace is recognized as Unicode
characters; placeholder bases must be nonnegative integers; integer values are
bounded to signed 64 bits; parser/compiler recursion exhaustion becomes `DSLError`.
Values remain separate bound parameters, and callers must enclose a compiled
expression before appending scope/type constraints. Regression tests exercise the
existing pure query-plan builders without an executor or database.

Verification uses synthetic inputs, exact SQL/value assertions, and an external
8,000-case ASCII tokenizer comparison derived from the checked-in Go source. The
comparison is a source-derived oracle, **not an executed Go differential test**.
Unicode whitespace is deliberately excluded from that ASCII comparison. No live
database, target, route or provider is involved.
