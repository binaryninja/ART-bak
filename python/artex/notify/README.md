# Notification port status

Offline port of upstream `notify` domain primitives: event/message data, severity
and status labels, tolerant filtering, channel metadata, credential masking and
safe configuration updates, UTF-8 truncation, whole-item batch packing, English
Markdown rendering, DingTalk/Feishu signatures, and static destination checks.

No network calls or transport implementations are present. Remaining work:
HTTP/SMTP channel validation and delivery, retry/error classification, redaction,
webhook Go-template compatibility, Feishu cards, Telegram/email HTML and MIME,
dial-time resolved-IP checks and redirect restrictions, and server/database
integration. Do not treat static URL validation as SSRF-safe delivery.

Intentional differences: display strings are translated; malformed filter fields
reset the entire filter per the upstream documented contract (Go's unmarshalling
can in practice partially populate it). Secret suffix hints use Unicode character
boundaries rather than risking invalid UTF-8 from a six-byte slice.

Tests use only the new Python code and the standard library. No original Go
code, install scripts, external endpoints, or credentials are executed/used.
