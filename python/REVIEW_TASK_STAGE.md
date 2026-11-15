# Independent task-stage review

Reviewed 2026-11-15 against the local English/Python migration working tree.
This is a scoped integration/parity review, not a full security audit or production
certification. No upstream Go code, database, provider, tools, or network was run.
Only authored Python port modules and inert test doubles were executed.

## Confirmed issues found and repaired during review

1. **HEAD routing/auth parity.** A public GET health endpoint returned 401 for
   HEAD, unlike Go ServeMux. `artex/server/core.py` now falls back from HEAD to
   GET, preserves explicit HEAD precedence and includes HEAD in Allow. WSGI
   suppresses the body. The independent review regression now passes.
2. **Malformed Unicode password produced 500.** A JSON password containing a
   lone surrogate reached UTF-8 encoding outside validation error handling.
   `artex/server/auth.py:validate_password` now rejects it as a client error
   before hashing or persistence. Independent regression verifies 400/no writes.
3. **Lossy database-to-manager conversion.** `Task.to_dict()` omits absent and
   empty fields for serialization. Using it for internal hydration caused
   `update_metadata` to raise KeyError after successfully renaming an unpinned
   task, and left a cached `queued_at` after pause cleared the database value.
   `artex/manager.py:_record` now uses lossless `dataclasses.asdict` for Task.
   Actual Task dataclass/inert repository regressions verify both behaviors.
4. **Missing required empty DTO arrays.** `company_ids` and `llm_profile_ids`
   disappeared through that same conversion, unlike non-omitempty Go TaskDTO.
   Lossless hydration plus `_dto` defaults now preserve both arrays. Independent
   regression passes. Source IDs remain stringified as expected by the UI.
5. **Pause/start reservation window.** The initial manager could snapshot no
   run, release its lock, then race a new start before persisting pause.
   Current `TaskManager.pause` reserves a cancelled run gate under the same
   lock even when no runner exists. This closes that in-process window; the
   worker's lifecycle tests cover reservations and cancellation. This review
   did not independently execute a multithreaded schedule.

## Reviewed contracts and useful negative findings

- Task creation wraps exploration, origin node, task, sources, scope snapshots
  and ordered LLM profiles in one executor transaction. It rejects unsupported
  nonempty interception rules rather than silently omitting them.
- Source limits (8), company limits (32 unique positive IDs), duplicate handling
  and profile ordering were compared with Go. Company snapshot SQL retains
  advisory lock 7337741003 and the important asset-task update before provenance
  upsert ordering from `db/tasks.go` / `db/companies.go`.
- HTTP create/control routes are protected by default, fail explicitly without
  required adapters, and preserve string source IDs versus numeric company and
  profile IDs. The manager keeps cancellation gated until runner drain is
  acknowledged. Failure cannot be represented as a successful running task.
- Settings initialization uses atomic insert-if-absent. PostgreSQL execution
  uses native bound parameters and a connection lock covering transactions;
  commit failures propagate before returning. No concrete auth bypass or
  SQL interpolation vulnerability was established in this scoped review.
- Stronger HS256/expiry checks, bounded request bodies, rejected invalid UTF-8,
  and redacted unexpected errors are intentional hardening, not parity bugs.

## Remaining implementation/integration boundaries

- `server/application.py` now explicitly registers TaskService and accepts
  task_manager/profile_available injection. Without a manager it returns 503.
  Factory wiring is not proof of a complete operational task API or runtime.
- The manager is explicitly single-process. Its lock/gates do not coordinate
  multiple managers or processes. `TaskStore.apply_admission` compares status,
  not a full lifecycle revision; do not deploy concurrent runtimes against the
  same task registry without stronger ownership/admission coordination.
- Real DTO metrics/archive enrichment, runner validation, selected-profile
  availability, scope/approval enforcement and cooperative cancellation still
  require adapters. Unit fakes validate interfaces, not those guarantees.
- Scheduler, restart/recovery, timeout monitoring, interception persistence,
  hard deletion/cross-store cleanup and execution-engine integration remain
  documented omissions. Unsupported requests must continue failing closed.
- No live PostgreSQL schema/trigger/driver integration, concurrent database
  transactions, end-to-end browser/API operation or Go/Python differential
  runtime tests were performed. The company-lock and transaction findings are
  static evidence plus fake contracts, not proof of deployed atomicity.

## Reproducible offline checks

`tests/test_task_stage_review.py` owns five independent regressions above.
At the reviewed snapshot these commands passed:

```sh
PYTHONPATH=python python -m unittest discover -s python/tests -p 'test_*task*.py' -q
# 69 tests
PYTHONPATH=python python -m unittest discover -s python/tests -p 'test_manager.py' -q
# 14 tests
```

These counts refer to the snapshot, not permanent coverage or complete parity.
