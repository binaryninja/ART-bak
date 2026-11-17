# Scoped skill creation UI

This slice completes top-level skill creation in the already scoped Python Skills
workspace. It does not start an agent, invoke a model/tool, install an archive,
grant visibility, connect MCP, or change backend authority. Only a host that has
explicitly enabled the bounded filesystem composer can perform the request.

## Wire contract

`POST /api/skills` must return HTTP 201 and `{name: submittedName}` without a
pending/unknown/no-retry envelope. Extra receipt fields are ignored. The shared
Go handler uses this contract too, and the existing fixture-only MOCK receipt
remains compatible. The API captures the submitted name before awaiting.

Names follow the existing Go/Python Unicode letter/digit rules: 1–64 code points,
letter first, ASCII letters lowercase, hyphens neither trailing nor doubled, and
at most 255 UTF-8 bytes. Non-ASCII letters and decimal digits are preserved.

The Python dialog sends only name, description, optional license, compatibility
and instructions. Empty optional values are omitted. MCP/visibility controls and
requests remain unavailable in that profile. Server-side metadata/body bounds,
quoted inert metadata rendering, create-only conflict handling, conservative
partial-write/unknown-outcome handling, explicit grants and root ownership remain
backend responsibilities, not client authorization.
Known 400/403/409/413 errors retain the form and allow a corrected explicit retry.

## Ownership and uncertainty

The published skill workspace model now owns a root-create request alongside its
file/folder write owners. Creation cannot overlap model-owned file/folder writes;
an existing uncertain file operation also blocks a new root creation. The root
request is fenced synchronously before any await or React render. Creation never
changes file selection or clears existing file drafts.

Network loss, unexpected success status, malformed/mismatched receipt, explicit
unknown/pending/no-retry flags, or a 5xx creation failure suspend all writes in the
mounted Skills workspace. Treating 503 as uncertain is conservative: the client
cannot independently prove a mutating request had no effect. Save, file/folder
creation and root creation enforce that suspension in model methods; existing
page-level ZIP/delete/metadata/visibility callbacks also check it synchronously.
Reads, catalog refresh and editing/copying drafts remain possible, but none
unlocks writes. There is no timeout, automatic retry or reset button.

The new-skill form stores an immediate latest-draft reference, separate from its
rendered state, and a dialog-session identity. Every edit, including same-value
edits, retires reset authority. A receipt clears only the unchanged submitted
draft, never newer edits. A reopened dialog is not closed by an earlier operation.
An unchanged submitted draft may clear after reopen, but that new dialog remains
open. Unmounted callbacks do not change fields, close views, refresh the catalog
or emit feedback. Effect replay restores the busy flag from the request owner.

For Go, confirmed primary creation is announced and the catalog refreshed before
optional visibility requests settle. A later visibility failure is reported as a
separate partial outcome, never as failed creation, and does not retry creation.
Existing optional Go operations that were already in flight are not canceled or
newly serialized by this slice. The Python path exposes none of those operations.

## Limits and validation

This is in-memory ownership for one mounted page, not durable idempotency or a
server CAS against externally deleted/reused names. Reloading/navigating away can
remove client state; the Python host's unknown-outcome root quarantine remains
authoritative. Existing cached-draft identity semantics for out-of-band namespace
changes and fixture-only MOCK persistence are not redesigned.

Focused tests run real API helper/model/callback source with synthetic responses,
including Unicode names, exact receipts, same-turn repeats, file/root exclusion,
unknown versus known refusals, later edits/reopen/unmount, effect replay, and Go
secondary failure. These tests are not mounted React or browser acceptance. Full
checks, actual type/build evidence, combined authenticated HTTP acceptance and
independent review are recorded against the final frozen tree outside this file.
No workaround for the previously denied browser startup is part of this work.
