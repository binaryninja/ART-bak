"""Explicit stamp-once source task clock, fenced by a prepared bootstrap receipt.

The source tasks table has no persisted run/config generation. An arbitrary
EngineScope or current callback therefore CANNOT authorize this operation. This
subset requires the Python bootstrap journal's exact prepared receipt, an actual
installed manager generation, and its host lifetime guard. Initialized graphs
without that journal are unsupported. No fallback IDs, schema changes, imports
with effects, engine deadline enablement or automatic retry after uncertainty.
Existing engine/bootstrap constructors still reject nonzero timeout tasks.

Source stamp_first_run uses transaction now(), COALESCE first_run_at, and never
moves an already stamped deadline. This API additionally compares stored timeout,
locks task before journal, and returns an explicit receipt only after commit and
final authority check. Failure after commit requires reconciliation, not retry.
"""
from dataclasses import dataclass
from ..agent.bootstrap import Receipt
from ..manager import TaskManager,CancellationToken
from .tasks import TaskStore
from .core import Query,int64

class DeadlineConflict(RuntimeError):pass
class DeadlineUncertain(RuntimeError):pass

@dataclass(frozen=True)
class DeadlineReceipt:
    task_id:int
    exploration_id:int
    run_id:int
    journal_revision:int
    first_run_at:object
    deadline_at:object
    timeout_seconds:int

class TaskDeadlineStore:
    def __init__(self,executor,*,manager,handle,receipt,lifecycle_guard,authorize):
        if type(receipt) is not Receipt or receipt.phase!='prepared':raise ValueError('Exact prepared bootstrap receipt required')
        if not isinstance(manager,TaskManager) or not isinstance(manager.repository,TaskStore):raise ValueError('Actual manager/task store required')
        from ..agent.bootstrap_handle import BootstrapHandle
        if type(handle) is not BootstrapHandle or type(handle.token) is not CancellationToken:raise ValueError('Actual installed engine owner required')
        if not callable(lifecycle_guard) or not callable(authorize) or lifecycle_guard is not handle.coordinator.guard:raise ValueError('Exact bootstrap lifetime guard and stamp authority required')
        for value in (receipt.task_id,receipt.exploration_id,receipt.run_id,receipt.revision):
            if int64(value,'deadline identity')<=0:raise ValueError('Positive generation required')
        self.executor=executor;self._executor=executor;self.manager=manager;self.handle=handle
        self.receipt=receipt;self.guard=lifecycle_guard;self.authorize=authorize
        self._repository=manager.repository;self._token=handle.token
        self._manager,self._handle,self._receipt=manager,handle,receipt
        self._coordinator,self._guard=handle.coordinator,lifecycle_guard
    def _check(self):
        r=self.receipt;h=self.handle
        if self.manager is not self._manager or h is not self._handle or r is not self._receipt:raise DeadlineConflict('Deadline owner changed')
        if self.executor is not self._executor or self.manager.repository is not self._repository or self._repository.executor is not self._executor or h.token is not self._token:raise DeadlineConflict('Deadline resource changed')
        if h.coordinator is not self._coordinator or self.guard is not self._guard or h.coordinator.guard is not self._guard:raise DeadlineConflict('Deadline lifetime changed')
        try:
            if h.validate_prepared_receipt(r) is not True:raise DeadlineConflict('Prepared receipt unavailable')
        except Exception:raise DeadlineConflict('Prepared deadline configuration changed') from None
        if h.executor is not self._executor or h.coordinator.journal.executor is not self._executor:raise DeadlineConflict('Bootstrap database changed')
        scope=h.scope
        if scope is None or (scope.task_id,scope.exploration_id,scope.run_id)!=(r.task_id,r.exploration_id,r.run_id):raise DeadlineConflict('Deadline generation mismatch')
        if self.manager.owns_generation(str(r.task_id),self._token,h) is not True:raise DeadlineConflict('Installed deadline owner unavailable')
        self._token.check()
    def validate_current(self,timeout_seconds):
        """Read-only exact clock revalidation; never stamps or adopts a receipt."""
        timeout_seconds=int64(timeout_seconds,'timeout seconds')
        if timeout_seconds<0:raise ValueError('Nonnegative timeout required')
        r=self.receipt
        with self.guard(r.task_id,r.exploration_id):
            self._check()
            if self.authorize(r,timeout_seconds) is not True:raise DeadlineConflict('Deadline read not authorized')
            self._check()
            with self._executor.transaction() as tx:
                rows=tx.fetch_all(Query("""SELECT timeout_seconds,first_run_at,deadline_at FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE""",(r.task_id,r.exploration_id)))
                if len(rows)!=1 or len(rows[0])!=3 or rows[0][0]!=timeout_seconds or rows[0][1] is None:raise DeadlineConflict('Current task clock unavailable')
                clock=rows[0]
                rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",
                    (r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
                if rows!=[(1,)]:raise DeadlineConflict('Durable deadline owner changed')
                self._check()
                result=DeadlineReceipt(r.task_id,r.exploration_id,r.run_id,r.revision,clock[1],clock[2],timeout_seconds)
            self._check()
            return result
    def stamp(self,timeout_seconds):
        timeout_seconds=int64(timeout_seconds,'timeout seconds')
        if timeout_seconds<0:raise ValueError('Nonnegative timeout required')
        r=self.receipt;committed=False;attempted=False
        try:
            with self.guard(r.task_id,r.exploration_id):
                self._check()
                if self.authorize(r,timeout_seconds) is not True:raise DeadlineConflict('Deadline stamp not authorized')
                self._check()
                with self._executor.transaction() as tx:
                    rows=tx.fetch_all(Query("""SELECT timeout_seconds FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE""",(r.task_id,r.exploration_id)))
                    if len(rows)!=1 or rows[0][0]!=timeout_seconds:raise DeadlineConflict('Task clock configuration changed')
                    rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",
                        (r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
                    if rows!=[(1,)]:raise DeadlineConflict('Durable deadline owner changed')
                    self._check();attempted=True
                    rows=tx.fetch_all(Query("""UPDATE tasks SET first_run_at=COALESCE(first_run_at,now()),
deadline_at=CASE WHEN first_run_at IS NOT NULL THEN deadline_at
WHEN $3>0 THEN now()+make_interval(secs=>$3) ELSE NULL END
WHERE id=$1 AND exploration_id=$2 AND timeout_seconds=$3 RETURNING first_run_at,deadline_at""",(r.task_id,r.exploration_id,timeout_seconds)))
                    if len(rows)!=1 or rows[0][0] is None:raise DeadlineConflict('Clock stamp unavailable')
                    self._check()
                    result=DeadlineReceipt(r.task_id,r.exploration_id,r.run_id,r.revision,*rows[0],timeout_seconds)
                committed=True;self._check()
                return result
        except DeadlineConflict:
            if committed:raise DeadlineUncertain('Clock committed; authority reconciliation required') from None
            raise
        except BaseException as exc:
            if attempted or committed:raise DeadlineUncertain('Clock persistence outcome requires reconciliation') from None
            if not isinstance(exc,Exception):raise
            raise DeadlineConflict('Clock preflight unavailable') from None
