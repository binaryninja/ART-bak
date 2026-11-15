# Skills port scope

Implemented offline, standard-library-only primitives:
- `valid_skill_name`, `skill_rel_path`, `clean_strs`, and upload size constants mirror `server/server_mgmt.go` contracts. Unicode identifiers remain accepted as data; all new source/documentation is English. Path size is measured as UTF-8 bytes, as in Go.
- `skill_name_from_frontmatter` mirrors the upload helper's permissive delimiter search.
- `rewrite_skill_frontmatter` mirrors the Go line-based metadata updater, preserving instruction body text and unknown lines. It is deliberately not a complete YAML parser.
- `render_skill` generates text only. It quotes scalar values to avoid malformed YAML and rejects multiline metadata. The Go scaffold interpolates values unquoted; this is an intentional safety improvement, not byte-for-byte output parity.
- `parse_skill` supports flat scalar metadata, quoted strings and comma-separated MCP names. Unsupported YAML structures, tags, aliases, multiline scalars and duplicate keys are rejected instead of silently misinterpreted.
- `Catalog` provides deterministic listing, lookup and allow-list selection. Duplicate names are rejected.

The Go project's `skill.Registry`, `skill.LoadDir` and actual invocation reside in the external norma SDK, whose implementation was not available in the repository. The Python catalog/parser is an explicitly limited adapter, **not SDK parity**. There are no dynamic imports, tool calls, subprocesses, network activity or skill execution. Markdown remains untrusted inert text.

Not implemented: full YAML semantics, filesystem discovery/mutation, symlink-safe filesystem transactions, ZIP extraction/compression compatibility/GBK fallback, upload HTTP handlers, usage ledger and invocation metering, MCP unlocking, plugin/runtime integration, or translation of bundled skill instruction files. Path validation alone must not be treated as symlink-safe authorization for a future file adapter.

Tests: `PYTHONPATH=python python -m unittest discover -s python/tests -p test_skills.py -v`.
