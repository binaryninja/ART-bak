# Inert runtime tuning configuration check

Separate entry point: PYTHONPATH=python python -m artex.runtime_config_check
--check-config FILE. Existing artex.__main__ and all runtime launch APIs unchanged.
This is syntax validation only, not an operator-ready launcher, readiness check,
credential check or execution authorization. No runtime/DB/provider modules are
imported; no connections, sockets, threads, migrations or writes are started.

Exact version1 JSON schema (all fields required):
{"version":1,"listener":{"host":"127.0.0.1","port":8787,"max_connections":8,
"socket_timeout":10},"pump":{"interval":1,"max_per_tick":8},
"reads":{"max_rows":500,"max_bytes":1048576}}

Limits match existing bounded APIs: port1..65535, connections1..32, socket timeout
0.1..60, pump interval0.01..60, ticks1..1000, read rows1..500, bytes1024..1048576.
Values tune nothing until an independently authorized future integration exists.
No serve flag, DSN/signing-key argument, profile/provider origin, environment
fallback, execution flag, transcript path or arbitrary factory import is accepted.

The pure parser accepts at most16KiB UTF-8 bytes; exact keys, duplicate-key
rejection (including escaped aliases), finite numbers and exact integer/bool
separation. Errors never echo values. CLI reads only a bounded regular file via
nonblocking/no-final-symlink open; special files/directories/final-component symlinks refuse. Ancestor-directory
symlinks are permitted: this is not a pathname confinement boundary. Exact option
spelling is required and duplicate --check-config flags reject before file access.
It does not certify filesystem snapshot authenticity or host policy. Fixed
argument/file/parse diagnostics suppress path and possible secret contents.

Eight tests cover valid syntax/pure no-I/O/no runtime imports, missing/extra keys,
credential/factory/execution rejection, duplicates/Unicode/NaN/depth/oversize,
types/ranges/loopback constraints, CLI no writes/redacted errors special-file refusal, duplicate/abbreviated option rejection and explicit
ancestor-symlink behavior. Independent review identified and verified correction of ambiguous CLI options;
exact flag spelling and single occurrence are now enforced. Legacy invocation
remains unchanged.

Scoped QA206 PASS:
`/workspace/shared/artex-qa/runtime-config-206/REPORT.md`
Manifest `cde03dfb0f83aa4a549a932da2f08b70e5dbd58f1fe08124df3b5de824b288ac`.
Frozen index plus checker/test overlays; all entries and checker/legacy source
hashes reverified.34 isolated subprocess cases covered valid tuning and endpoints,
invalid types/bounds/host, malformed/duplicate/deep/oversize JSON, unknown secrets,
final symlink/FIFO/directory/device/missing file, exact option behavior, help and
legacy environment checks. Abbreviations/duplicates failed before config-file
open; no synthetic secret marker appeared in output. FIFO refused promptly.
19 focused tests passed (8 new plus11 legacy), not a full-suite run.

Python audit hooks and thread guards observed zero forbidden effects in the34
cases, with no runtime/server/database/provider/agent imports by the new module.
The read-only snapshot used -B/PYTHONDONTWRITEBYTECODE to exclude interpreter cache
writes. Intended input-file and normal import reads still occurred. Legacy check
retained0/2 behavior with synthetic DSN/key values only; no database launched.
This is Python-level evidence, NOT kernel syscall absence or native-extension proof.

OS strace could not run because PTRACE_TRACEME was denied; no bypass was attempted.
Socket special-file creation was also denied, so that fixture was not tested.
Earlier203 socket setup failure,204 ptrace failure and205 duplicate test-name
collision remain preserved.206 corrected the harness naming and completed after
environment recovery. No launch/readiness or full-suite acceptance claim.

Actual runtime launch remains blocked on code-owned lifecycle/exclusive ownership,
provider origin/credential policy, transcript recovery and unresolved-shutdown
supervision. Callback-presence checks or DSN/key syntax cannot create that authority.
Read-only design: /workspace/shared/artex-qa/runtime-launch-assessment/PROPOSAL.md.
No staging/publication/deployment.
