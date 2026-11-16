# Legacy schema messages: presentation-only English

The new web/src/lib/legacy-display-text.ts maps exactly two known task-asset
provenance summaries and one known retest-conversation-deleted error. Asset and
retest mappings are separate, so a message in the wrong context is not translated.
Unknown text, prefixes/suffixes, custom evidence and whitespace variants pass
through unchanged. Mapping occurs before the existing asset-cell trim/fallback.

Consumers: task detail assets-tab.tsx, sessions-tab.tsx, and
components/finding-retest-panel.tsx. No API object mutation, DB/schema rewrite,
migration, deployment, wire-key change or historical evidence replacement occurs.
Other clients/raw API exports can still see the original stored messages.

The scope-text-editor placeholder now says `ICP filing number` rather than a
Chinese example. This is instructional placeholder text, not an invented valid
registration number. ICP matchers and stored values are unchanged.

Two newly authored pure utility tests pass in Node, covering exact mappings,
unknown/prototype-key preservation, wrong-context strings and immutable input
objects. No existing application module, Next plugin or build script was executed.
TypeScript 5.9.3 parsed all six changed/new TS/TSX/MJS files with zero syntactic
diagnostics; file hashes and results are retained as QA evidence. This is not
semantic type checking, a full frontend build, or browser validation. Independent
source-contract review cleared with two additional unchanged-schema/consumer tests;
four pure utility tests pass in total.
