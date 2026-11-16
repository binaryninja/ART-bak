# Optional catalog GET composition

`bind_catalog_reads` composes an original authenticated WSGI application with an
exact ChatMentionService and explicit global catalog grant. The original app is
unchanged. A protected literal GET `/api/chat/mentions` declaration is registered
only on the returned view; conflicting routes are rejected before callbacks.
No report service, task fallback, mention expansion or external send is implied.

An original-issued prepared receipt retains the exact normalized selection,
response and all 21 candidate asset IDs, including cursor lookahead. Final
disclosure never reconstructs authority from only the 20 visible rows. Original
JWT and original source/asset/current/request gates are checked after headers;
the original outer catalog grant is refreshed after source callbacks and only
pure pinned receipt/request/resource checks follow. The known late-current
revocation regression is preserved. This finite cooperative order is not an
atomic fixed point against arbitrarily mutually mutating policy callbacks.

After headers, refusal aborts body delivery; it cannot retract an already-issued
status. Copied receipts, substituted resources and changed selectors are denied.
No default application or database schema is changed.

Stage131 passes three actual PostgreSQL/JWT groups and 27 focused tests, including
all lookahead asset grants, pagination, pre-SQL refusal and late disclosure
revocation. See ISOLATED_QA.md for the exact manifest and limits.
