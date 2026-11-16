"""Explicit tracked frontier completion; no runner hook or positive admission.

Waiting/busy/expired observations allocate no terminal capability. Once a store
is attempted, uncertainty is sticky. Confirmed retirement remains tracked until
actual original runner removal is observed; this module never mutates runner
inventory or invokes unrelated tasks' completion callbacks.
"""
from dataclasses import dataclass,fields,asdict
from threading import Lock,Event
from .host import AgentHost
from .bootstrap_handle import BootstrapHandle
from .engine_runner import EngineRunner
from ..db.task_deadline import TaskDeadlineStore
from ..db.early_completion import EarlyCompletionGrant,EarlyCompletionStore
from ..db.unstamped_completion import UnstampedCompletionStore
from ..db.core import Query

class FrontierUnavailable(RuntimeError):pass
class FrontierUncertain(FrontierUnavailable):pass

@dataclass(frozen=True)
class FrontierCompletionPolicy:
    revision:int
    grant:object
    authorize:object
    def __post_init__(self):
        if type(self.revision) is not int or self.revision<=0 or type(self.grant) is not EarlyCompletionGrant or not callable(self.authorize):raise ValueError('Explicit scoped frontier policy required')

@dataclass(frozen=True)
class FrontierCompletionResult:
    phase:str
    receipt:object=None

class OwnedFrontierCompletion:
    def __init__(self,host,handle,deadline_store,policy,manual_owner):
        from .manual_action_owner import ManualActionOwner
        if type(host) is not AgentHost or type(host.runner) is not EngineRunner or type(handle) is not BootstrapHandle or type(deadline_store) is not TaskDeadlineStore or type(policy) is not FrontierCompletionPolicy or type(manual_owner) is not ManualActionOwner:
            raise ValueError('Exact owned frontier components required')
        policy.__post_init__()
        if manual_owner.host is not host or deadline_store.manager is not host.manager or deadline_store.handle is not handle or handle.manager is not host.manager or deadline_store.executor is not host.manager.repository.executor:raise ValueError('Original frontier host/resources required')
        self.host=host;self.handle=handle;self.deadline=deadline_store;self.policy=policy
        self._pins=(host,host.manager,host.runner,handle,handle.token,deadline_store,deadline_store.executor,deadline_store.receipt,deadline_store.guard,manual_owner)
        self._policy_pins=tuple(getattr(policy,f.name) for f in fields(policy));self._policy=policy
        self._current=handle.coordinator.current;self._handles=host.runner.handles
        from ..manager import TaskManager
        self._retirement_snapshot=TaskManager.retirement_snapshot
        self._lock=Lock();self._cancel=Event();self._active=False;self._used=False;self._failed=False
        self._store=None;self.result=None;self._retirement=None;self._removal_pending=False
        self._tracking_lock=Lock();self._manual_owner=None;self._manual_owner_pin=None;self._manual_started=False
    def _binding(self):
        h,m,r,handle,token,d,e,receipt,guard,owner=self._pins
        if self.host is not h or h.manager is not m or h.runner is not r or m.runner is not r or self.handle is not handle or handle.token is not token or self.deadline is not d or d.manager is not m or d.handle is not handle or d.executor is not e or m.repository.executor is not e or d.receipt is not receipt or d.guard is not guard or handle.coordinator.current is not self._current:
            raise FrontierUnavailable('Frontier resources changed')
        if self.policy is not self._policy or any(getattr(self.policy,f.name) is not value for f,value in zip(fields(self.policy),self._policy_pins)) or r.handles!=self._handles:raise FrontierUnavailable('Frontier policy or inventory changed')
        if self._manual_owner is not owner or self._manual_owner_pin is not owner:raise FrontierUnavailable('Frontier tracking required')
        owner.validate_bindings()
    def _allow(self,operation):
        self._binding();h,m,r,handle,token,d,e,receipt,guard,owner=self._pins
        if self._cancel.is_set():raise FrontierUnavailable('Frontier action cancelled')
        d._binding_check()
        if not m.owns_generation(str(receipt.task_id),token,handle):raise FrontierUnavailable('Original running generation unavailable')
        handle.coordinator._check(receipt.task_id,receipt.exploration_id,handle.policy_revision)
        if self._policy_pins[2](receipt,operation) is not self._policy_pins[1]:raise FrontierUnavailable('Frontier operation denied')
        handle.coordinator._check(receipt.task_id,receipt.exploration_id,handle.policy_revision)
        self._binding();d._binding_check();token.check()
        self._policy_pins[1]._validate(receipt,operation)
        if self._cancel.is_set() or not m.owns_generation(str(receipt.task_id),token,handle):raise FrontierUnavailable('Frontier ownership revoked')
    def _enter(self,operation):
        with self._tracking_lock:
            self._binding();self._manual_started=True
            return self._manual_owner_pin._enter(self,operation)
    def cancel(self):
        with self._lock:
            if self.result is not None:return False
            self._cancel.set()
        self._policy_pins[1].revoke()
        return True
    def _candidate(self):
        self._allow('read');h,m,runner,handle,token,d,e,r,guard,owner=self._pins
        if handle.wait(0) is not True:return 'busy',None
        if handle.unresolved is not False:raise FrontierUnavailable('Unresolved owner cannot complete')
        if not any(v is handle for v in self._handles()):raise FrontierUnavailable('Original runner handle absent')
        lease=d.lease
        if lease is not None and (not m.validate_deadline_owner(lease) or m.deadline_owner_phase(lease)!='attached'):raise FrontierUnavailable('Original attached lease required')
        with guard(r.task_id,r.exploration_id):
            self._allow('read')
            with e.transaction() as tx:
                rows=tx.fetch_all(Query("""SELECT timeout_seconds,first_run_at,deadline_at,
CASE WHEN deadline_at IS NULL THEN NULL ELSE deadline_at<=clock_timestamp() END FROM tasks
WHERE id=$1 AND exploration_id=$2 AND status='running' AND NOT paused AND NOT queued
AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE""",(r.task_id,r.exploration_id)))
                if len(rows)!=1 or len(rows[0])!=4:raise FrontierUnavailable('Current frontier task unavailable')
                timeout,first,deadline,expired=rows[0]
                if type(timeout) is not int or timeout<=0 or timeout!=handle.prepared_timeout_seconds:raise FrontierUnavailable('Prepared positive timeout mismatch')
                if lease is None:
                    if first is not None or deadline is not None or expired is not None:raise FrontierUnavailable('Cannot adopt an unowned clock')
                    branch='unstamped'
                else:
                    c=lease.clock
                    if (first,deadline,timeout)!=(c.first_run_at,c.deadline_at,c.timeout_seconds) or type(expired) is not bool:raise FrontierUnavailable('Original clock changed')
                    branch='expiry_required' if expired else 'stamped'
                rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",(r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
                if rows!=[(1,)]:raise FrontierUnavailable('Prepared frontier receipt changed')
                nodes=tx.fetch_all(Query("SELECT id,kind,CASE WHEN octet_length(state)<=32 THEN state ELSE NULL END AS state FROM exploration_nodes WHERE exploration_id=$1 AND kind IN ('goal','intent') ORDER BY id LIMIT 10001 FOR SHARE",(r.exploration_id,)))
                if len(nodes)>10000 or any(type(i) is not int or i<=0 or k not in ('goal','intent') or type(s) is not str for i,k,s in nodes) or len({i for i,k,s in nodes})!=len(nodes):raise FrontierUnavailable('Invalid bounded frontier')
                active=any((k=='goal' and s=='open') or (k=='intent' and s in ('open','running')) for i,k,s in nodes)
            self._allow('read')
        self._allow('read');self._binding()
        if d.lease is not lease:raise FrontierUnavailable('Clock changed during candidate read')
        return ('expiry_required' if branch=='expiry_required' else 'waiting' if active else branch),lease
    def run_once(self):
        receipt=self._enter('run_once');owner=self._manual_owner_pin
        try:
            with self._lock:
                if self._active or self._used or self._failed:raise FrontierUnavailable('Frontier completion cannot replay')
                self._active=True
            try:
                phase,lease=self._candidate()
                if phase in ('busy','waiting','expiry_required'):return FrontierCompletionResult(phase)
                self._allow('reserve')
                self._used=True
                cls=EarlyCompletionStore if phase=='stamped' else UnstampedCompletionStore
                self._store=cls(self.deadline,grant=self._policy_pins[1],authorize=self._policy_pins[2])
                result=self._store.settle()
                self.result=result
                self._retirement=(self._store,result,tuple(asdict(result).values()),self._store._cap,self._store._pins,self._store._grant,type(self._store._cap),tuple(asdict(self._store._cap).values()))
                self._removal_pending=True
                self._binding()
                return FrontierCompletionResult('awaiting_runner_removal',result)
            except BaseException:
                if self._used:self._failed=True;raise FrontierUncertain('Frontier attempt retained; do not replay') from None
                raise
            finally:
                with self._lock:self._active=False
        finally:owner._leave(receipt)
    def retirement_result(self,runner):
        """Return original committed evidence for the exact runner, without effects.

        Call outside runner/host/registry locks. This does not remove inventory,
        query SQL, run grants, wait for a handle, or restore execution permission.
        Revoking a permit after acknowledged retirement cannot undo that result.
        The runner must still verify actual drain and compare original identities.
        """
        from ..manager import TaskManager,CancellationToken
        from ..db.early_completion import EarlyCompletionReceipt
        from ..db.unstamped_completion import UnstampedCompletionReceipt
        self._binding()
        with self._lock:
            if runner is not self._pins[2] or self._active or self._failed or not self._used or not self._removal_pending or self._retirement is None:
                raise FrontierUnavailable('Exact pending retirement required')
            store,result,values,cap,pins,grant,cap_type,cap_values=self._retirement
            expected=EarlyCompletionReceipt if type(store) is EarlyCompletionStore else UnstampedCompletionReceipt if type(store) is UnstampedCompletionStore else None
            if (type(result) is not expected or self._store is not store or self.result is not result
                or tuple(asdict(result).values())!=values or store._result is not result
                or store._cap is not cap or type(cap) is not cap_type or tuple(asdict(cap).values())!=cap_values
                or cap.task_id!=str(result.task_id) or store._pins is not pins or store._grant is not grant
                or store.grant is not grant or store.deadline is not self._pins[5]
                or store.authorize is not self._policy_pins[2] or store._evidence is not None
                or getattr(store,'_retired_slot',None) is not None or store._used is not True):
                raise FrontierUnavailable('Committed retirement evidence changed')
            manager=self._pins[1];token=self._pins[4];prepared=self._pins[7]
            if (result.task_id,result.exploration_id,result.run_id,result.bootstrap_revision,result.config_digest)!=(prepared.task_id,prepared.exploration_id,prepared.run_id,prepared.revision,prepared.config_digest) or result.status!='done' or result.reason!='frontier_drained':
                raise FrontierUnavailable('Retirement generation mismatch')
        # Invoke the original code-owned read methods, not replaceable host
        # policy/current callbacks or instance-provided inventory adapters.
        if TaskManager.retirement_snapshot is not self._retirement_snapshot:raise FrontierUnavailable('Original retirement reader changed')
        snapshot=self._retirement_snapshot(manager,str(result.task_id))
        if snapshot!=('done',result.completed_at.isoformat(),False,False) or not CancellationToken.cancelled.fget(token):
            raise FrontierUnavailable('Registry retirement is not confirmed')
        self._binding()
        with self._lock:
            if self._active or self._failed or self._retirement is None or self._retirement[1] is not result or self.result is not result or not self._removal_pending:
                raise FrontierUnavailable('Retirement continuation changed')
        return result

    def confirm_runner_removal(self):
        """Read-only continuation, permitted after seal for already retired work.

        No inventory mutation or call to unrelated tasks' settlement is made.
        The future runner hook must remove the exact original handle itself.
        """
        receipt=self._enter('confirm_runner_removal');owner=self._manual_owner_pin
        try:
            with self._lock:
                if self._active or self._failed or self.result is None or not self._removal_pending:raise FrontierUnavailable('No pending exact runner removal')
                self._active=True
            try:
                values=self._handles();self._binding()
                if type(values) not in (tuple,list) or len(values)>100000:raise FrontierUnavailable('Invalid runner inventory')
                if any(v is self._pins[3] for v in values):return False
                self._removal_pending=False
                return True
            finally:
                with self._lock:self._active=False
        finally:owner._leave(receipt)
