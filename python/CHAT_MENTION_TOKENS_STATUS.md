# Visible chat mention token parsing

`artex.chat_mention_tokens.parse_chat_mentions` ports the pure token-selection
contract from `server/chat_mentions.go`. It accepts the source's 18 English and
historical labels, including Application/App, and returns immutable references
in first-appearance order. Deduplication uses canonical kind and numeric ID;
leading zeros and aliases therefore do not consume extra slots. The first alias
is retained for display. Labels after the ID are ignored, never treated as names,
record content, instructions or authorization.

Recognized zero or signed-int64 overflow IDs reject the complete parse. Unknown
labels and malformed token syntax remain ordinary text, as in Go. Ten unique
references are allowed. Long leading-zero IDs are normalized before integer
conversion to retain Go behavior without Python's decimal-digit conversion cap.
Python non-string or unpaired-surrogate input deliberately fails closed. Message
size remains the caller/transport's responsibility; no new size limit is invented.

Eleven authored offline tests pass, covering every label against the reviewed Go
source map, legacy fixtures, alias/ID deduplication, numeric boundaries, long
zeros, unique-reference limits, ignored display text, immutability and malformed
syntax. This is a static source-contract fixture plus Python unit verification,
not execution of Go or proof of frontend round-trip integration.

No existing HTTP behavior changes. Mention record loading, snapshot bounding,
prompt expansion, authorization and chat admission integration are not provided;
existing unsupported-mention rejection remains in place. A parsed reference alone
must never grant access to the referenced record or authorize execution. No DB,
provider, target, filesystem snapshot or network call is added.

Independent review caught quadratic backtracking in the initial full-token regex
on repeated unterminated display fields. The corrected parser separates prefix
recognition from a cached next-bracket/line-boundary scan. Both positions advance
monotonically, retaining source leftmost/non-overlapping and nested-token behavior
without imposing a new input cap. Regression tests count searched spans at 1,000,
2,000 and 4,000 nested openers (at most twice input length), cover newline/closing
boundaries, and compare 1,500 seeded short inputs against the reviewed source
regex. An illustrative local timing run for 1k/2k/4k/8k unterminated openers took
0.00034/0.00072/0.00196/0.00308 seconds; these are not benchmark guarantees.
