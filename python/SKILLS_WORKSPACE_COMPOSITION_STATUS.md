# Coherent explicit Skills workspace composition

`bind_skills_workspace` joins the already reviewed catalog, file listing, file
content read, file upsert and directory creation adapters into one application.
All five original binders receive the same untouched exact WSGI/Router/JWT base.
This resolves their exact-base requirement without stacking wrappers or replacing
any adapter's own source, request, global, revision or mutation authority.

## Routing and contracts

- `GET /api/skills`: original inert filesystem catalog DTO.
- `GET/HEAD /api/skills/{name}/files`: original bounded file listing.
- `GET/HEAD /api/skills/{name}/files/{file...}`: original content reader.
- `PUT /api/skills/{name}/files/{file...}`: original UTF-8 upsert, 200 `{ok:true}`.
- `POST /api/skills/{name}/dirs`: original nonrecursive mkdir, 201 `{dir:path}`.

Known-family unsupported methods authenticate through the original JWT and return
405 with the exact supported Allow header. Catalog HEAD remains explicitly
unsupported in this narrow composer; file listing/content HEAD retain their
reviewed behavior. Other paths delegate unchanged to the original base, including
health/login. No upload, delete, top-level skill creation, metadata, visibility,
usage ledger, MCP installation or execution capability is created.

## Shared ownership without inherited permission

The exact same `SkillDirectoryScope` object and the same captured exclusive guard
callable must belong to all five services/readers. The catalog must use the exact
content reader supplied for file reads; no usage/SQL reader is accepted. A second
scope, guard, content reader or mutable binding is rejected. The host must capture
one bound guard callable once and reuse that object, not take fresh bound-method
objects at each constructor call.

All five global grants are mandatory and independent. Read permission never
substitutes for a mutation grant; the original write and mkdir services retain
exact request/proposal authorization at every primitive phase. The composer
creates no policies, root, authentication state, files, lease, database connection
or listener. Base route registration is untouched, and a late bind failure leaves
no partial routes mounted. Factory/delegate/base method and policy pins remain
checked before dispatch, around the real header callback and during iteration.
It does not buffer or replace a mutation receipt, retry a write or translate an
unknown outcome into success.

The host must actually own the declared private tree, serialize all cooperative
writers/renames/mount and revision changes, and control request admission/shutdown.
A local mutex alone cannot exclude editors, scripts or other external writers.
The separate explicit development launcher supplies the ownership/transport and
uncertainty-retention policy; this factory by itself is not a safe launcher.
The existing source adapters' finite observations and no-hostile-writer-CAS limits
remain. PUT is upsert: "new file" can replace an existing file. In-request version
checks detect observed concurrent changes, not stale browser drafts or an ETag
precondition that this API does not have.

Catalog DTO calls/tasks/usage_agents placeholders remain unchanged for frontend
compatibility when no ledger is bound. The Python workspace UI must hide usage
statistics and say unavailable, not present those placeholders as measured zeros.
Unsupported controls must remain hidden/disabled in the Python profile.

## Evidence and remaining gate

Eleven tests cover inert and atomic construction, one scope/guard, independent
family grants, method routing/Allow, read-create-overwrite-reload, duplicate mkdir,
symlink and revocation, stale in-request targets, post-header authority/binding
changes and unchanged unknown500/no-retry receipts. One actual loopback test uses
the existing bounded hardened server, Host/Origin/framing handler, real bcrypt,
original AuthService and JWT login, then catalog/read/mkdir/file-create/upsert/
reload/HEAD against one private temporary tree. No SQL, provider or shell tool is
used; this is not fake-SQL end-to-end evidence. The launcher needs its own genuine
lease/admission/quarantine/framing/stop probes and combined exact-tree review.

No default CLI mount, filesystem mutation outside fixtures, publication, merge,
deployment, production-readiness or full Go parity claim is made. Full frozen-tree
testing and independent review remain gates.
