"""Single-host shared direct/FIFO capacity authority; explicit ticks only.

Bind facade admission_guard to this object's admission_guard. Use this same
object as FIFOScheduler store AND host, with scheduler.limit==controller.limit.
All admissions must use this authority; direct AgentHost admission bypass is
unsupported. Actual EngineRunner inventories include bootstrap and unresolved
handles: drained/idle does not mean completed or free capacity.

One bounded admission is reserved before any mutation. Calls to host/store/runner
occur outside the state mutex, avoiding callback/host lock inversion. Concurrent
or reentrant admissions fail fast. Post-call reconciliation releases only the
transient reservation, not retained runner ownership. Failed/busy reconciliation
quarantines further admission until explicit successful reconcile(). No hidden
threads, model calls, queues, distributed lease or readiness in construction.
Runner factories must honor their inert/no-detached-work contract; inventory
cannot account for work hidden by a violating custom factory. Deadlines remain
unsupported/deferred as in LocalHostAdapter. No automatic recovery of a lost host.
"""
from contextlib import contextmanager
from threading import Lock
from weakref import WeakKeyDictionary
from math import isfinite
from .host import AgentHost,PollResult
from .engine_runner import EngineRunner
from ..manager import RuntimeUnavailable
from ..scheduler import LocalHostAdapter,TaskSnapshot

_owners=WeakKeyDictionary()
_owners_lock=Lock()

class AdmissionController:
    def __init__(self,host,*,limit=1,single_process=False):
        if type(host) is not AgentHost or type(host.runner) is not EngineRunner or single_process is not True:
            raise RuntimeUnavailable('Exact single-process engine host required')
        if type(limit) is not int or not 1<=limit<=host.runner.max_pending:raise ValueError('Bounded capacity must not exceed runner capacity')
        self.host,self._manager,self._runner=host,host.manager,host.runner
        self.limit=limit;self._lock=Lock();self._busy=False;self._quarantined=False
        self._claim=object();self._adapter=LocalHostAdapter(host,single_process=True)
        with _owners_lock:
            if host in _owners:raise RuntimeUnavailable('Host already has a capacity authority')
            _owners[host]=self._claim
    def _identity(self):
        if self.host.manager is not self._manager or self.host.runner is not self._runner or self._manager.runner is not self._runner:
            raise RuntimeUnavailable('Admission host identity changed')
        with _owners_lock:
            if _owners.get(self.host) is not self._claim:raise RuntimeUnavailable('Admission authority changed')
    def _reserve(self,*,reconcile=False):
        self._identity()
        with self._lock:
            if self._busy or (self._quarantined and not reconcile):return False
            self._busy=True;return True
    def _release(self,*,quarantine=False):
        with self._lock:
            self._quarantined=quarantine;self._busy=False
    def _poll_owned(self):
        self._identity();state=self.host.poll();self._identity()
        if type(state) is not PollResult or type(state.busy) is not bool or state.busy:
            raise RuntimeUnavailable('Host occupancy reconciliation is busy')
        if any(type(n) is not int or n<0 for n in (state.running,state.pending)) or state.running+state.pending>self._runner.max_pending:
            raise RuntimeUnavailable('Invalid bounded host occupancy')
        return state
    @contextmanager
    def admission_guard(self,operation,task_id):
        if operation not in ('create','start','resume','queued'):raise ValueError('Unknown admission operation')
        if (operation=='create' and task_id is not None) or (operation!='create' and (type(task_id) not in (str,int) or not str(task_id).isascii() or not str(task_id).isdecimal() or not 0<int(task_id)<2**63)):
            raise ValueError('Exact task identity required')
        if not self._reserve():raise RuntimeUnavailable('Admission capacity authority is busy or quarantined')
        entered=False;inspected=False;quarantine=False
        try:
            state=self._poll_owned();inspected=True
            if state.running+state.pending>=self.limit:raise RuntimeUnavailable('Task capacity exhausted')
            entered=True
            yield True
        finally:
            # Reconcile on successful, failed and cancelled hosted mutation.
            # Do not mask the original failure; quarantine remains observable.
            try:
                if entered:self._poll_owned()
            except Exception:
                quarantine=True
            except BaseException:
                quarantine=True;raise
            finally:self._release(quarantine=quarantine or not inspected)
    def reconcile(self):
        """Explicitly retry bounded host settlement/inventory after uncertainty."""
        if not self._reserve(reconcile=True):raise RuntimeUnavailable('Admission reconciliation is busy')
        good=False
        try:state=self._poll_owned();good=True;return state
        finally:self._release(quarantine=not good)
    def poll(self):
        if not self._reserve():return PollResult(True)
        good=False
        try:state=self._poll_owned();good=True;return state
        finally:self._release(quarantine=not good)
    def scheduler_snapshot(self,limit):
        self._identity()
        if type(limit) is not int or not 1<=limit<=10000:raise ValueError('Bounded scheduler scan required')
        result=self._adapter.scheduler_snapshot(limit);self._identity();return result
    def ready(self,snapshot):
        self._identity()
        if type(snapshot) is not TaskSnapshot:raise ValueError('Exact task snapshot required')
        value=self._adapter.ready(snapshot);self._identity();return value
    def admit_queued(self,snapshot):
        if type(snapshot) is not TaskSnapshot:raise ValueError('Exact task snapshot required')
        try:
            with self.admission_guard('queued',snapshot.task_id):return self._adapter.admit_queued(snapshot)
        except RuntimeUnavailable:return 'deferred'
    def settle_deadline(self,snapshot,now):
        self._identity();return 'deferred'
    def drain(self,timeout):
        if type(timeout) not in (int,float) or not isfinite(timeout) or timeout<0:raise ValueError('Finite nonnegative drain timeout required')
        if not self._reserve(reconcile=True):raise RuntimeUnavailable('Admission authority is busy')
        good=False
        try:
            self._identity();result=self.host.drain(timeout);self._poll_owned();good=True;return result
        finally:self._release(quarantine=not good)
