# Unified opt-in skill-read composition

`bind_skill_reads` composes the existing usage, missing-statistics and filename-
listing overlays against one original JWT WSGI application. These individual
binders deliberately require an exact base WSGIApplication and cannot be stacked
on each other's wrapper objects. The new binder preserves those requirements by
constructing three independent views and dispatching their disjoint path families.
It adds no new route semantics, default wiring or filesystem/ledger permissions.

All three services and three outer grants must be explicitly supplied. A file-
listing permit never implies usage/missing-ledger permission; each source scope,
request grant, source-global grant and lifecycle/current policy is retained. Only
the selected family's policies and I/O run. Retiring an unrelated family does
not block the selected family. Binding runs original validators only, never
source policy callbacks, SQL or filesystem access. A late-family conflict fails
without changing the original route table or publishing a partial composition.

Code-owned delegate classes, call/check/constructor methods and imported binders
are anchored at module load, rather than trusting whichever implementation is
present when binding is requested. Pre-bind implementation replacement refuses
without invoking a source policy or I/O. This does not claim protection against
an already-compromised Python import environment or arbitrary private-state edits.
Selected view identity, exact type, original call/check methods and original base
application are pinned before dispatch and rechecked before returning its body.
Changes during start_response refuse the prepared body. Each existing overlay
continues to own its pre/post-header authorization and disclosure checks.
Unrelated paths delegate to the original app. All methods select the same family
as its original overlay, preserving HEAD refusal and method errors.

Necessary existing-overlay hardening: usage and missing-statistics wrappers now
repeat their original JWT check after final outer/request authority callbacks,
matching the late-clock-expiry protection added to file listing. No other source
reader semantics or grant order was changed. Direct standalone regressions and
the combined app both verify that final callbacks advancing the stable original
clock beyond JWT expiry refuse body delivery.

Validation: 14 new tests (12 composition, 2 direct late-JWT regressions) and all 145
skill-focused tests pass. Coverage includes three-family reachability, independent
permissions, JWT-before-I/O, inert/atomic binding, unrelated routing, unrelated-
family retirement, selected family replacement before/after headers, selected
post-header revocation, foreign-base refusal, pre-bind delegate/binder replacement and late outer clock
expiry in every family.

The authored tests above use inert database adapters and disposable POSIX trees.
Independent re-review subsequently cleared the corrected composition, including
the original pre-bind bypass reproduction, binder replacement, post-header class
call replacement and invalid-third-service binding atomicity.

**Corrected PG+FS155 acceptance** passed five actual integrated groups:
1. Three opt-in genuine JWT routes returned real PostgreSQL found-usage and
   hot/cold missing statistics plus real filesystem names. Original-app 404,
   missing-JWT denial before I/O and health/unknown delegation were retained.
2. Family-specific permission isolation, independent request/source/current
   denial, and permitted access despite retirement of an unrelated family.
3. Final outer-callback advancement of the JWT clock refused bodies in all
   three families after 200 headers had already been observed.
4. Post-header selected/request/source/current revocation and family replacement
   withheld bodies; before-call replacement returned 503; late-family route
   conflict failed without partial base-route mutation.
5. Pre-bind changes to each delegate's call/check/constructor methods (nine
   cases), all three imported binders, foreign-original composition and later
   foreign-base substitution refused before reads.

All 145 skill-focused tests passed on the immutable snapshot; the three source
hashes matched afterward. Manifest SHA256:
`a4ced33055302b493a49872301b2e81b81f4ef00b0e6c8c90addb02de3b92842`.
Composite SHA256:
`47edfe45430cf6d8156a6076991c54b13d1ae9f035f7c8e8074c8e40faeb1d4d`.
Usage setup SHA256:
`75f6f024f438e5818d7fc1a004c48c0f2738c3a9376db9cfedc717029cf22371`.
Missing setup SHA256:
`477e39b796964b5017c6cf7887245789238c9ff6bc4267e7277f815590d11903`.
The earlier PG154 four-group baseline is retained but did not cover the pre-bind
integrity defect and is not final acceptance of the corrected implementation.

PG+FS155 used fresh original-schema PostgreSQL 17 and temporary files in a
credentialless user/filesystem/network namespace, UID1000, loopback only, with no
external route or host workspace/home access. Inputs were synthetic metadata and
files; WSGI/JWT composition was real, but no network listener/browser, provider,
target or skill execution was involved. It changed no shared source/index and
published nothing. This establishes only the described combined-component cases.

Cooperative lifecycle/observer and filesystem-writer constraints still
apply; neither repeated observations nor this dispatcher provides a universal
concurrency/TOCTOU guarantee, production scheduler or full frontend parity.
