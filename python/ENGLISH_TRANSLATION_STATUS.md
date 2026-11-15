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
