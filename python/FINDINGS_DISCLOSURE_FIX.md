# Scoped findings final-disclosure correction

Published checkpoint `600ffa2cbb4b48f98bd5eee13715169f00e92046` could return a
scoped finding when the final asset callback or lifecycle-guard exit revoked a
previously approved source grant. Two unchanged exact-interface regressions
reproduced both cases against the published source in an isolated subprocess.

The correction revalidates the exact source set after asset/request callbacks
and rechecks disclosure after guard exit. It adds no mutation, permissions or
nested guard. The response is withheld on either revocation. This is a concrete
boundary fix, not a guarantee against arbitrarily self-mutating policy callbacks.

Independent review cleared both regressions and neighboring tests. Disposable
PostgreSQL stage85 verified a positive anchored finding and both late revocation
cases through the actual direct-source relationship and asset projection: both
negative cases returned403 without finding or asset data. Twelve focused tests
also passed. Immutable manifest:
`25e20e9ce49c90dc70550f3b17a5ae77a90f51132d82268b073b05f201bf865c`.

This checkpoint contains this correction only, not the subsequent assessment
journal or coverage features under development. Full port and deployment remain
incomplete.
