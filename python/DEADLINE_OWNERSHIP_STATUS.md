# Process-local deadline ownership: partial internal integration

This slice connects manager reservations, store-issued one-shot persistence evidence,
retained clock leases and host/runner retirement. It does not enable positive
runtime task timeouts. Normal bootstrap construction still refuses them.

A lease is separate from an execution token. A suspended lease can retain an exact
clock identity after a drained pause, while the old token cannot execute, resume,
complete or adopt another generation. Attempted stamp/install uncertainty retains
quarantined ownership and blocks implicit retry. A pending reservation prevents
retirement from discarding the only possible clock owner. Lease inventory is
separate from execution-capacity accounting, but unresolved work is not reported
fully settled.

SQL and handle callbacks must not run under the manager registry lock. Independent
review caught pause persistence violating this order; the correction uses exact
cancelled-generation checks before and after persistence outside the lock.

Clock identity must match the constructor-pinned prepared timeout as well as task,
journal and manager identities. Tests of positive internal lease transitions use
explicit synthetic prepared fixtures. Changing only the task row from zero to a
positive timeout is not valid prepared authority. Those tests are not evidence that
an ordinary user can start a timed task through the runtime.

Resume transfer, terminal expiry/final-assessment coordination, crash recovery,
multi-process ownership and production timer dispatch remain unimplemented. No
caller-created receipt, self-certifying callback or retained cancelled token is
substituted for these capabilities. See DEADLINE_LEASE_HANDOFF.md for the design
and ISOLATED_QA.md for the exact acceptance evidence when available.

Public positive clock stamping now delegates to the owned registration handshake;
positive validation requires its exact registered clock. The zero-timeout helper
also compares the original prepared timeout, so it cannot turn a prepared positive
clock into an unlimited run. Genuine prepared-zero helpers retain their SQL-free
behavior. These checks close alternate-path bypasses found during review, without
removing the normal bootstrap/engine positive-timeout rejection.
