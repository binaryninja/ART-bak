# Isolated ARTEX baseline and Python PostgreSQL QA

## Scope and safety
- Original Go source commit: 7ad252ad3b891c2ab3fe747da509feb149166e8f (exported archive, no checkout edits).
- Python snapshot: 5444aa4578f3c73fcee162cc2fb60bfa1abfec76.
- Source TestMain/build setup inspected before execution. No frontend/npm/build.sh workflows run.
- Outer user+network namespace, only loopback and no routes; inner bwrap PID/user/IPC/UTS namespaces, UID1000, cleared environment, selected readonly runtime mounts and dedicated QA directories. No user workspace/home or credentials mounted. No live targets/provider keys.
- Go1.26.3 official archive SHA256 verified: 2b2cfc7148493da5e73981bffbf3353af381d5f93e789c82c79aff64962eb556.
- Modules downloaded separately using official Go module proxy/checksum database. Test runtime GOPROXY=off, GOTOOLCHAIN=local, no proxy variables.
- PostgreSQL17.11 Debian13 official signed-index packages extracted, not host-installed. Ephemeral data in sandbox /tmp, TCP only on isolated127.0.0.1; server stopped afterward. Unix sockets are denied by host policy.

## Original Go results
`go test -p 2 -timeout 8m -json ./...`:
- 903 pass events, 2 fail events, 1 skip event (test counts include named subtests).
- Package results: 13 passed, 2 failed, 2 no-test packages skipped.
- FAIL agent/TestGraphOverviewExpandsAssociatedCompanyScope: blackboard_inheritance_test.go:87, `task scope missing: <nil>`.
- FAIL server/TestCoreTaskLifecyclePG: core_test.go:88, `expected goal nodes seeded, got 0`.
- Live provider TestLiveContextReview intentionally skipped because ARTEX_REVIEW_LIVE_CONFIG absent.

Repeated complete suite with `-p 1 -count=1`: same903/2/1 results and same failures.
Focused isolated rerun of the two test names: agent failure reproduced; server lifecycle test passed. This suggests server suite-state/order sensitivity, but root cause is unproven. Neither failure is a Python-port regression: all runs used unmodified original source.

## Python real PostgreSQL smoke
psycopg3.3.9 binary driver, RawCursor, Python3.13, PG17.11, original schema loaded via direct driver setup. Seven explicit checks passed:
1. Native $1/$2 parameter binding, including quote-containing text.
2. Transaction exception rollback.
3. Nested savepoint rollback with successful outer commit.
4. Category create/get.
5. Task create and read/hydration.
6. Constraint create/read/update/delete.
7. Task enqueue/dequeue/status persistence.

This is smoke coverage, NOT comprehensive schema/store/locking parity. No production migrations or deployments occurred. Deadlocks, concurrent cross-process stores, all104 SQL statements, every task options combination, service/provider/Norma runtime and frontend remain unverified. PostgresExecutor deliberately rejects DDL rowcount -1: schema setup used direct driver, not executor.


The test harness was disposable; the commands above require equivalent isolated dependency and schema preparation. No raw test logs or credentials are included in this checkpoint.

## Extended asset/finding checks and correction

Published `0dd89f3f8f829da2caa5161382e145982aaef0ba` passed 11 targeted
PostgreSQL checks covering six asset types (seven variants), API provenance,
repeat-write identity, rollback, finding metadata mirroring and report-version
checks. One additional check exposed a port defect: schema-valid `archive_failed`
was incorrectly blocked because the port compared against `failed`.

After correction, immutable local snapshot
`ff9071d3840a2a685a1b169bc64c68356d44d857`, tree
`d3e1d8cefc9e6d47eb771e2d153ab9046ca06b88`, passed all 12 checks in the same
isolated PostgreSQL environment. Failed archives now allow report updates and
finding creation; queued archives still block mutation and preserve prior data.
This snapshot identifier is local validation evidence, not a claimed remote commit.

Fixture company resolution returns no company, mutation guards are inert and
root-domain resolution is a deterministic mapping. Production policy adapters,
public suffix handling, notification/evidence workflows and concurrent lock
contention remain unverified. Fixture domains/addresses were never contacted.

## Profile and node-board real PostgreSQL checks

The exact tree `dad29d5c007eabc1c6ec638674b117ba15917abf` (published as
`ecece212d8309c3e6798fd30e00f56328a7571ec`) passed eight additional isolated
PostgreSQL fixture checks: profile public/private masking, blank-key preservation,
active selection and missing-ID rollback, explicit unsupported deletion guard,
node paging/search/scope, inherited intent filtering, edges and anchors.
Dummy credentials only; no provider calls. Profile deletion, concurrent activation,
failover repair, pagination snapshot consistency and broader parity remain unproven.

## Composed WSGI and real bcrypt checks

The exact tree `60696dfbfe34269986ba4d5a46538c4a43e22f2c` (published as
`1effa5b36f8307626adda57d2017f6bd198b59bc`) passed six grouped integration
checks using PostgreSQL17.11, psycopg3.3.9 and PyCA bcrypt5.0.0: auth initialization,
duplicate-init rejection and hashing; login/JWT/protected routes; real profile
list masking and unavailable runtime response; scoped graph/not-found responses;
node-board neighbor and anchor composition. Tests invoked WSGI directly without a
listener. Dummy credentials and explicit fixture resolvers only. This does not
establish production hosting, provider integration or comprehensive interoperability.

## Company, scheduler and actual loopback transport checks

Immutable local snapshot `da4d7b3cff5990daab8917e5d1d525f96ff29032` passed six
grouped checks: company normalized upsert and scoped domain/IP/ICP resolution;
real TaskStore FIFO admission/completion; pause and stale-generation rejection;
and actual HTTP transport POST against a fixture server inside the isolated
loopback network. No external provider, user credentials or target was contacted.
Later skill-label-only edits did not change tested code. Distributed scheduling,
company scope mutations and external-provider interoperability remain unverified.

## Side-question, worker and company HTTP checks

Immutable local snapshot `376dc0a91124aa6e28c53ccda02674f244c4d5f9` passed seven
grouped PostgreSQL checks: three side-question parent kinds, snapshot ordering,
idempotency and clear-generation rejection of late writes; foreign worker
rejection; worker claim/state version and transcript CAS; lifetime/archive guards;
and composed company API authentication, create/list/conflict/unsupported scope.
Only disposable fixture history was cleared. Lifetime guards and transcript
backends were fixtures, and ordering checks were sequential, not concurrency
proof. No optional profile factory/provider execution was performed in this run.

## Go/Python authentication interoperability

On immutable `36e88a4b61cd1468d54b7922e80a0ab0ec6031a8`, bcrypt hashes and ordinary
seven-day HS256 login JWTs verified in both directions using source-equivalent
Go helpers and Python authentication code. Both reject expired/future-not-before
tokens. Both accept signed arbitrary audience/subject and future issued-at claims;
these are not configured authorization selectors in the single-admin model.

Confirmed stricter Python differences: it requires expiration and accepts HS256
only; original Go accepts missing expiration and the broader HMAC family including
HS512. Ordinary source-generated HS256 tokens interoperate. Dummy keys only; no
keys, hashes usable as credentials, or tokens are published in this report.

## Streaming, retry and side-runtime integration

Immutable local snapshot `386010389537a906b69386e96f43cab3fad5914e` passed six
grouped checks in isolation: real PostgreSQL template atomic patch/FK rollback and
fixture deletion; actual side-question coordinator/service/store preserving partial
usage and enforcing profile/clear-generation guards; real retry transport/provider
against loopback HTTP (429 then success used two calls; refusal used one); and
composed authenticated WSGI log SSE replay/live delivery/close. No product mismatch
was found. Provider behavior was a fixture and retry waits bypassed; external
provider compatibility, timing behavior and production streaming remain unverified.
