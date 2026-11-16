"""Explicit stamp-once source task clock, fenced by a prepared bootstrap receipt.

The source tasks table has no persisted run/config generation. An arbitrary
EngineScope or current callback therefore CANNOT authorize this operation. This
subset requires the Python bootstrap journal's exact prepared receipt, an actual
installed manager generation, and its host lifetime guard. Initialized graphs
without that journal are unsupported. No fallback IDs, schema changes, imports
with effects, engine deadline enablement or automatic retry after uncertainty.
Existing engine/bootstrap constructors still reject nonzero timeout tasks.
Owned registration also requires the constructor-owned prepared timeout pin;
changing only manager/SQL timeout cannot adopt a positive prepared clock.

Source stamp_first_run uses transaction now(), COALESCE first_run_at, and never
moves an already stamped deadline. This API additionally compares stored timeout,
locks task before journal, and returns an explicit receipt only after commit and
final authority check. Failure after commit requires reconciliation, not retry.
stamp_owned connects that commit to a manager reservation and one-shot evidence;
validate_lease is a distinct read-only suspended-owner operation. These helpers
do not install a timer, resume attachment, or expiry/final-assessment coordinator.
A preexisting stamped deadline is preserved verbatim (including legacy timeout
changes); no exact first_run+timeout equality is invented. Manager validation
requires aware ordered dates and the separately pinned timeout configuration.
"""
from dataclasses import dataclass
from threading import Lock
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
        self._registration_lock=Lock();self._evidence_lock=Lock()
        self._evidence={};self._lease=None;self._registration_unresolved=False;self._attempted_reservation=None
    def _binding_check(self):
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
    def _check(self):
        self._binding_check();r=self.receipt;h=self.handle
        if self.manager.owns_generation(str(r.task_id),self._token,h) is not True:raise DeadlineConflict('Installed deadline owner unavailable')
        self._token.check()
    def _authorize_exact(self,authorizer,timeout_seconds,check):
        if self.authorize is not authorizer:raise DeadlineConflict('Deadline policy changed')
        check()
        if authorizer(self._receipt,timeout_seconds) is not True:raise DeadlineConflict('Deadline operation not authorized')
        if self.authorize is not authorizer:raise DeadlineConflict('Deadline policy changed')
        check()

    def validate_current(self,timeout_seconds):
        """Read-only exact clock revalidation; never stamps or adopts a receipt."""
        timeout_seconds=int64(timeout_seconds,'timeout seconds')
        if timeout_seconds<0:raise ValueError('Nonnegative timeout required')
        r=self.receipt;authorizer=self.authorize;lease=self._lease
        def check():
            self._check()
            if timeout_seconds:
                if self.handle.prepared_timeout_seconds!=timeout_seconds or lease is None or self._lease is not lease:
                    raise DeadlineConflict('Exact prepared clock lease required')
                if lease.receipt is not r or lease.clock.timeout_seconds!=timeout_seconds or self.manager.validate_deadline_owner(lease,operation='read') is not True:
                    raise DeadlineConflict('Registered clock owner unavailable')
        with self.guard(r.task_id,r.exploration_id):
            self._authorize_exact(authorizer,timeout_seconds,check)
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
                check()
                result=DeadlineReceipt(r.task_id,r.exploration_id,r.run_id,r.revision,clock[1],clock[2],timeout_seconds)
                if timeout_seconds and result!=lease.clock:raise DeadlineConflict('Registered task clock changed')
            self._authorize_exact(authorizer,timeout_seconds,check)
            return result
    def stamp(self,timeout_seconds):
        timeout_seconds=int64(timeout_seconds,'timeout seconds')
        return self.stamp_owned(timeout_seconds) if timeout_seconds>0 else self._stamp(timeout_seconds)
    def _stamp(self,timeout_seconds,*,reservation=None):
        timeout_seconds=int64(timeout_seconds,'timeout seconds')
        if timeout_seconds<0:raise ValueError('Nonnegative timeout required')
        r=self.receipt;committed=False;attempted=False;authorizer=self.authorize
        try:
            with self.guard(r.task_id,r.exploration_id):
                self._authorize_exact(authorizer,timeout_seconds,self._check)
                with self._executor.transaction() as tx:
                    rows=tx.fetch_all(Query("""SELECT timeout_seconds FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE""",(r.task_id,r.exploration_id)))
                    if len(rows)!=1 or rows[0][0]!=timeout_seconds:raise DeadlineConflict('Task clock configuration changed')
                    rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",
                        (r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
                    if rows!=[(1,)]:raise DeadlineConflict('Durable deadline owner changed')
                    self._authorize_exact(authorizer,timeout_seconds,self._check)
                    if reservation is not None:
                        self.manager.mark_deadline_attempted(reservation)
                        self._attempted_reservation=reservation
                    attempted=True
                    rows=tx.fetch_all(Query("""UPDATE tasks SET first_run_at=COALESCE(first_run_at,now()),
deadline_at=CASE WHEN first_run_at IS NOT NULL THEN deadline_at
WHEN $3>0 THEN now()+make_interval(secs=>$3) ELSE NULL END
WHERE id=$1 AND exploration_id=$2 AND timeout_seconds=$3 RETURNING first_run_at,deadline_at""",(r.task_id,r.exploration_id,timeout_seconds)))
                    if len(rows)!=1 or rows[0][0] is None:raise DeadlineConflict('Clock stamp unavailable')
                    self._check()
                    result=DeadlineReceipt(r.task_id,r.exploration_id,r.run_id,r.revision,*rows[0],timeout_seconds)
                committed=True;self._authorize_exact(authorizer,timeout_seconds,self._check)
                return result
        except DeadlineConflict:
            if committed:raise DeadlineUncertain('Clock committed; authority reconciliation required') from None
            raise
        except BaseException as exc:
            if attempted or committed:raise DeadlineUncertain('Clock persistence outcome requires reconciliation') from None
            if not isinstance(exc,Exception):raise
            raise DeadlineConflict('Clock preflight unavailable') from None

    @property
    def lease(self):
        """Exact manager capability, never a model/tool execution permission."""
        return self._lease

    def validate_reservation_owner(self):
        """Pure original execution-owner validation; manager calls outside its lock."""
        self._check()
        return True

    def consume_stamp_evidence(self,reservation,evidence):
        """Consume only private evidence issued after this store's committed stamp.

        Caller-provided DeadlineReceipt values are deliberately insufficient.
        This is an in-process capability contract, not a hostile-code sandbox.
        """
        with self._guard(self._receipt.task_id,self._receipt.exploration_id):
            self._check()
            with self._evidence_lock:
                item=self._evidence.get(evidence)
                if item is None or item[0] is not reservation:
                    raise DeadlineConflict('Unrecognized clock persistence evidence')
                del self._evidence[evidence]
                result=item[1];authorizer=item[2]
            self._authorize_exact(authorizer,result.timeout_seconds,self._check)
            return result

    def stamp_owned(self,timeout_seconds):
        """Stamp and register one positive clock with the installed manager owner.

        Reservation exists before SQL; attempted writes retain quarantine on any
        failure. No retry, fresh-token adoption, or implicit timeout enablement.
        """
        timeout_seconds=int64(timeout_seconds,'timeout seconds')
        if timeout_seconds<=0:raise ValueError('Positive owned timeout required')
        if not self._registration_lock.acquire(False):raise DeadlineConflict('Clock registration already active')
        reservation=None;evidence=None;authorizer=self.authorize
        try:
            self._binding_check()
            if self.handle.prepared_timeout_seconds!=timeout_seconds:raise DeadlineConflict('Timeout differs from prepared configuration')
            if self._registration_unresolved:raise DeadlineUncertain('Clock registration requires reconciliation')
            if self._lease is not None:raise DeadlineConflict('Clock lease already installed')
            reservation=self.manager.reserve_deadline_owner(str(self.receipt.task_id),self._token,self.handle,
                store=self,timeout_seconds=timeout_seconds)
            if self.authorize is not authorizer:raise DeadlineConflict('Deadline policy changed')
            result=self._stamp(timeout_seconds,reservation=reservation)
            # Successful commit is the only issuance point. Manager installation
            # independently checks these dates and the full original identity.
            evidence=object()
            with self._evidence_lock:self._evidence[evidence]=(reservation,result,authorizer)
            self._lease=self.manager.install_deadline_owner(reservation,evidence)
            return result
        except BaseException:
            if reservation is not None:
                attempted=self._attempted_reservation is reservation
                try:
                    if attempted:self.manager.quarantine_deadline_owner(reservation)
                    else:self.manager.abort_deadline_reservation(reservation)
                except BaseException:
                    attempted=True
                    try:self.manager.quarantine_deadline_owner(reservation)
                    except BaseException:pass
                if attempted:
                    self._registration_unresolved=True
                    raise DeadlineUncertain('Clock registration outcome requires reconciliation') from None
            raise
        finally:
            if evidence is not None:
                with self._evidence_lock:self._evidence.pop(evidence,None)
            self._registration_lock.release()

    def validate_lease(self,lease):
        """Lease-only read of an immutable clock, including a suspended owner.

        This never calls owns_generation, clears cancellation, stamps, or grants
        execution. Manager exact-capability validation is required around SQL.
        """
        if lease is not self._lease or lease is None:raise DeadlineConflict('Exact retained lease required')
        r=self._receipt;expected=lease.clock;authorizer=self.authorize
        if self.handle.prepared_timeout_seconds!=expected.timeout_seconds:raise DeadlineConflict('Leased timeout configuration changed')
        def check():
            self._binding_check()
            if self.manager.validate_deadline_owner(lease,operation='read') is not True:
                raise DeadlineConflict('Clock lease unavailable')
            phase=self.manager.deadline_owner_phase(lease)
            if phase not in ('attached','suspended'):raise DeadlineConflict('Clock lease not readable')
            if lease.receipt is not r or expected is not lease.clock:raise DeadlineConflict('Clock identity changed')
            return phase
        with self._guard(r.task_id,r.exploration_id):
            phase=check()
            self._authorize_exact(authorizer,expected.timeout_seconds,check)
            if check()!=phase:raise DeadlineConflict('Clock lease phase changed')
            with self._executor.transaction() as tx:
                rows=tx.fetch_all(Query("""SELECT timeout_seconds,first_run_at,deadline_at FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND paused=$3 AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE""",(r.task_id,r.exploration_id,phase=='suspended')))
                if len(rows)!=1 or tuple(rows[0])!=(expected.timeout_seconds,expected.first_run_at,expected.deadline_at):
                    raise DeadlineConflict('Leased task clock changed')
                rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",
                    (r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
                if rows!=[(1,)]:raise DeadlineConflict('Leased durable owner changed')
                if check()!=phase:raise DeadlineConflict('Clock lease phase changed')
            if check()!=phase:raise DeadlineConflict('Clock lease phase changed')
            self._authorize_exact(authorizer,expected.timeout_seconds,check)
            if check()!=phase:raise DeadlineConflict('Clock lease phase changed')
            return expected

    def observe_expiry(self,lease,*,token,handle,freeze=None):
        """Exact full-owner DB-clock observation, never stamp or final authority."""
        if lease is None or lease is not self._lease or token is not self._token or handle is not self._handle:
            raise DeadlineConflict('Original observation owner required')
        r=self._receipt;c=lease.clock;authorizer=self.authorize
        def check():
            self._binding_check()
            if self._lease is not lease or lease.receipt is not r or lease.clock is not c or handle.prepared_timeout_seconds!=c.timeout_seconds:
                raise DeadlineConflict('Observed clock changed')
            return self._manager.validate_deadline_observation(str(r.task_id),token,handle,lease,freeze)
        paused=check()
        self._authorize_exact(authorizer,c.timeout_seconds,check)
        with self._guard(r.task_id,r.exploration_id):
            if check()!=paused:raise DeadlineConflict('Observed pause changed')
            with self._executor.transaction() as tx:
                rows=tx.fetch_all(Query("""SELECT timeout_seconds,first_run_at,deadline_at,deadline_at<=clock_timestamp()
FROM tasks WHERE id=$1 AND exploration_id=$2 AND status='running' AND paused=$3
AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE""",(r.task_id,r.exploration_id,paused)))
                if len(rows)!=1 or len(rows[0])!=4 or tuple(rows[0][:3])!=(c.timeout_seconds,c.first_run_at,c.deadline_at) or type(rows[0][3]) is not bool:
                    raise DeadlineConflict('Observed persisted clock changed')
                expired=rows[0][3]
                rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",
                    (r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
                if rows!=[(1,)]:raise DeadlineConflict('Observed bootstrap changed')
                if check()!=paused:raise DeadlineConflict('Observed pause changed')
            self._authorize_exact(authorizer,c.timeout_seconds,check)
        self._authorize_exact(authorizer,c.timeout_seconds,check)
        if check()!=paused:raise DeadlineConflict('Observed pause changed')
        return expired
