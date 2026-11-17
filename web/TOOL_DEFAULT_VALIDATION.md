# Tool parameter default validation

The ToolEditor previously converted any unrecognized boolean text to `false`,
silently removed a numeric default on invalid text, allowed fractional integer
defaults, and serialized non-finite numbers as JSON `null`. These were successful
metadata saves with different values from the user's intended edit. Both source
Go `pgUpdateTool` and Python `ToolCatalogService` accept schema annotations;
client-side conversion must not silently corrupt them.

The existing `coerceDefault`/`applyRows` path now rejects invalid numeric and
boolean input before `api.saveTool` is dispatched. Errors identify the parameter,
including array-item subfields, without echoing its raw value. The existing save
catch leaves the editor/draft open and restores its non-saving state.

- Blank/whitespace-only input still deliberately removes the default.
- Nonblank string values remain literal, including surrounding whitespace.
- Boolean `true`/`false` is case-insensitive; other text is an explicit error.
- Numbers must be finite and nonzero decimal literals cannot silently underflow
  to zero. Normal JavaScript finite-number precision remains in effect.
- Integers must be mathematically integral and within JavaScript's safe integer
  range. Signed values, decimal trailing zeros and exponent notation still work.
  Decimal fractions rounded by `Number` into integers are rejected rather than
  saved as a different value. Exponents are examined without expanding them.
- Schema structure, required flags, other annotations and non-scalar defaults
  are preserved. Validation uses a clone and does not partially change the
  original schema if a later parameter is invalid.

This is scalar input validation, not full JSON Schema constraint validation
(enum, minimum/maximum and other constraints), arbitrary-precision number
support, server authorization, mutation deduplication or a tool execution path.
The separate custom-tool editor, test execution and reset lifecycle are unchanged.

Tests execute the exact source conversion helpers and save closure, including
nested fields, invalid-save zero dispatch, valid typed JSON payloads, empty
clears, numeric boundary/rounding cases and unchanged save callbacks. These are
source-derived control-flow tests, not mounted React/browser acceptance. Real
TypeScript/build, aggregate checks and independent review are recorded separately
against the frozen candidate.
