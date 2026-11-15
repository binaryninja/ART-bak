# Findings, extended assets and explicit runner checkpoint

This checkpoint adds offline-tested adapters, not a production ARTEX replacement.

- Asset persistence and HTTP support all six upstream types. Mutations retain
  company lock, mutation guard and provenance writes in one transaction. Hostnames
  require an injected offline root-domain resolver; there is no guessed public
  suffix list or DNS call. Unsupported metadata fails before batch writes.
- Finding storage supports scoped list/detail, creation, metadata/status primitives
  and evidence-version-aware report writes. Report writes take the evidence lock,
  task lock and finding row lock in order. This is not the complete RecordFinding
  pipeline. Low-level status writes deliberately have no notification side effect.
- Authenticated finding HTTP implements standalone list/detail/metadata changes.
  Nonempty asset references require an explicit resolver. Status changes, live
  provenance, exports, deletion, retests and other missing workflows return 503;
  ten registered finding paths are unavailable stubs, not completed features.
- Application factories now register findings. Persisted composition injects a
  FindingStore and accepts explicit asset/root-domain resolvers; it performs no
  startup finding reads, provider calls or automatic task execution.
- The cooperative Runner binds one bounded conversation and synchronous result
  sink to a real worker thread. Cancellation is not drain. The host must settle
  completed workers only after manager admission returns; exact generation tokens
  prevent stale completion. No automatic scheduler or factory runner is enabled.

Independent review found and fixed direct-store invalid task selectors silently
broadening to global. HTTP had already rejected those inputs. Regression tests
now verify rejection before database access. See REVIEW_FINDING_RUNNER_STAGE.md.

The prior graph checkpoint remains a historical snapshot. Live PostgreSQL and
original Go validation is being conducted separately in a disposable environment;
no live parity is claimed by the offline tests in this checkpoint. Full planner,
worker orchestration, notification/evidence workflows, production transports,
recovery, UI interoperability and much of the Go API remain incomplete.
