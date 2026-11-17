"""Installed initialization → engine owner. Construction is inert.

One outer handle and exact manager token own both phases. First explicit advance
allocates a durable bootstrap journal generation and runs its GoalPlanner; only
a verified prepared receipt permits inert child construction. Child execution
starts on a later tick. No prepared receipt adoption after restart/new token,
background thread, automatic resume, or task completion from model end-turn.

The host must serialize graph/policy/lifecycle writers with coordinator.guard.
Factory purity is a trusted host obligation, not Python containment. Any error
after attempting initialization retains an unresolved owner; no retry. Child
construction uncertainty also remains owned. A constructed child is retained
before checking cancellation. Cleanup-only manager ownership never authorizes
model/tool execution. No deadlines or first-intent seeding are implemented.
"""
from copy import deepcopy
from threading import Condition,RLock,get_ident
from time import monotonic
import math
from .bootstrap import BootstrapCoordinator,BootstrapConflict,BootstrapUnresolved
from .engine import TaskEngineHandle,EngineScope,EngineProgress
from ..manager import TaskManager,CancellationToken
from ..db.tasks import TaskStore
from ..sidequestion import Model

class BootstrapHandle:
    def __init__(self,task,token,*,coordinator,manager,model,system,policy_revision,request_id,engine_factory,timed_admission=None):
        if type(token) is not CancellationToken or not isinstance(coordinator,BootstrapCoordinator) or not isinstance(manager,TaskManager) or not isinstance(manager.repository,TaskStore):raise ValueError('Actual coordinator, manager repository and token required')
        if not isinstance(task,dict) or not callable(engine_factory):raise ValueError('Explicit task snapshot and inert engine factory required')
        raw=task.get('id')
        if isinstance(raw,str) and raw.isascii() and raw.isdecimal():raw=int(raw)
        exp=task.get('exploration_id')
        if type(raw) is not int or not 0<raw<2**63 or type(exp) is not int or not 0<exp<2**63:raise ValueError('Valid persisted task/exploration required')
        if task.get('seed_first_intent'):raise ValueError('First intent seeding unavailable')
        if timed_admission is not None:
            from .timed_admission import PreparedTimedAdmission
            if type(timed_admission) is not PreparedTimedAdmission or any(getattr(getattr(timed_admission,name,None),'__func__',None) is not getattr(PreparedTimedAdmission,name) for name in ('validate_bindings','validate_handle_configuration')):
                raise ValueError('Exact original timed admission capability required')
        if task.get('timeout_seconds',0)!=0:
            if timed_admission is None:raise ValueError('Positive timeout requires explicit timed admission')
            timed_admission.validate_handle_configuration(task,manager,coordinator,policy_revision,model,system=system,request_id=request_id)
        elif timed_admission is not None:raise ValueError('Zero timeout does not use timed admission')
        if not isinstance(model,Model) or not model.identity or not isinstance(system,str) or not system.strip() or not isinstance(policy_revision,str) or not 1<=len(policy_revision)<=256 or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('Explicit bootstrap configuration and request ID required')
        if manager.repository.executor is not coordinator.journal.executor:raise ValueError('Manager and bootstrap must share executor')
        self.timed_admission=self._timed_admission=timed_admission
        self._admission_check=None if timed_admission is None else timed_admission.validate_bindings
        self.task_id=str(raw);self.exploration_id=exp;self.token=token
        self.model,self.system,self.policy_revision,self.request_id=model,system,policy_revision,request_id
        self.manager,self.coordinator=manager,coordinator
        self._task=deepcopy(task);self._factory=engine_factory
        self._manager,self._coordinator,self._token=manager,coordinator,token
        self._repository=manager.repository;self._executor=manager.repository.executor
        self._configuration=(model,system,policy_revision,request_id,str(raw),exp,task.get('timeout_seconds',0))
        self._condition=Condition(RLock());self._active=False;self._thread=None
        self._phase='installed';self._child=None;self._receipt=None;self._unresolved=False;self._completed=False
        self._continuation=self._continuation_pin=None
    @classmethod
    def _from_continuation(cls,owner,cap,task,token,old):
        from .task_continuation import TaskContinuationOwner
        if cls is not BootstrapHandle or type(owner) is not TaskContinuationOwner or type(old) is not BootstrapHandle:raise BootstrapConflict('Original continuation owner required')
        receipt=TaskContinuationOwner.construction_receipt(owner,cap,old,token)
        handle=cls(task,token,coordinator=old.coordinator,manager=old.manager,
            model=old.model,system=old.system,policy_revision=old.policy_revision,
            request_id=old.request_id,engine_factory=old._factory)
        handle._continuation=handle._continuation_pin=(owner,cap,receipt)
        return handle
    @property
    def prepared_timeout_seconds(self):
        """Constructor-owned timeout; positive requires verified explicit cap."""
        return self._configuration[-1]
    @property
    def executor(self):return self._executor
    @property
    def scope(self):
        with self._condition:
            return None if self._receipt is None else EngineScope(int(self.task_id),self.exploration_id,self._receipt.run_id,self.policy_revision)
    @property
    def unresolved(self):
        with self._condition:return self._unresolved or (self._child is not None and self._child.unresolved)
    @property
    def phase(self):
        with self._condition:return self._phase
    def _binding(self):
        if self._continuation is not self._continuation_pin:raise BootstrapConflict('Continuation binding changed')
        if self.timed_admission is not self._timed_admission:raise BootstrapConflict('Timed admission changed')
        if self._timed_admission is not None:
            if self._timed_admission.validate_bindings!=self._admission_check:raise BootstrapConflict('Timed admission validation changed')
            self._admission_check()
        if self.manager is not self._manager or self.coordinator is not self._coordinator or self.token is not self._token or self.manager.repository is not self._repository or self._repository.executor is not self._executor or self.coordinator.journal.executor is not self._executor:
            raise BootstrapConflict('Bootstrap owning resource changed')
        if (self.model,self.system,self.policy_revision,self.request_id,self.task_id,self.exploration_id,self._task.get('timeout_seconds',0))!=self._configuration:raise BootstrapConflict('Bootstrap configuration changed')
    def validate_construction(self):
        """Validate pinned inert bindings before installation, without SQL/work.

        Cancellation during construction remains retainable by the runner. This
        check is not installed-generation authority and never grants execution.
        """
        with self._condition:
            self._binding()
            if (self._phase!='installed' or self._active or self._child is not None
                or self._receipt is not None or self._unresolved or self._completed):
                raise BootstrapConflict('Bootstrap construction is no longer inert')
        return True
    def validate_prepared_receipt(self,receipt):
        """Match retained preparation and pinned config; not execution authority.

        Callers still require installed generation and the exact coordinator
        lifetime guard. No SQL, provider calls or receipt adoption occurs here.
        """
        with self._condition:
            self._binding()
            from .bootstrap import Receipt
            if (type(receipt) is not Receipt or self._receipt is None or receipt!=self._receipt
                or self._phase not in ('prepared','engine') or self._unresolved or self._completed
                or (self._child is not None and self._child.unresolved)):
                raise BootstrapConflict('Exact retained prepared receipt required')
        return True
    def _owned(self,*,allow_cancelled=False):
        self._binding()
        if self.manager.owns_generation(self.task_id,self.token,self,allow_cancelled=allow_cancelled) is not True:raise BootstrapConflict('Bootstrap generation not installed')
        if not allow_cancelled:self.token.check()
        self._binding()
    def _admitted_check(self):
        with self._condition:
            if self._phase!='initializing' or not self._active or self._thread!=get_ident():raise BootstrapConflict('Bootstrap admission phase unavailable')
        self._owned()
    def cancel(self,reason='paused_by_user'):
        self.token.cancel(reason)
        with self._condition:child=self._child
        if child is not None:child.cancel(reason)
    def wait(self,timeout=None):
        if timeout is not None and (isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<0):raise ValueError('Finite nonnegative timeout required')
        deadline=None if timeout is None else monotonic()+timeout
        with self._condition:
            if self._active and self._thread==get_ident():return False
            if not self._condition.wait_for(lambda:not self._active,timeout):return False
            child=self._child
        return True if child is None else child.wait(None if deadline is None else max(0,deadline-monotonic()))
    def advance(self,now):
        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now) or now<0:raise ValueError('Finite nonnegative time required')
        with self._condition:
            if self._active:raise BootstrapConflict('Bootstrap handle already advancing')
            if self.unresolved:raise BootstrapUnresolved('Bootstrap requires reconciliation')
            if self._completed:return EngineProgress('completed',completion_ready=True)
            if self.token.cancelled:return EngineProgress('cancelled',cancelled=True)
            self._owned()
            self._active=True;self._thread=get_ident()
            initial=self._phase=='installed'
            if initial:self._phase='initializing'
        try:
            if initial:
                if self._continuation is None:
                    receipt=self.coordinator.run_admitted(self)
                else:
                    from .task_continuation import TaskContinuationOwner
                    owner,cap,expected=self._continuation
                    receipt=TaskContinuationOwner.activate(owner,cap,self)
                    if receipt is not expected:raise BootstrapConflict('Original continuation receipt changed')
                with self._condition:self._receipt=receipt;self._phase='prepared'
                self._owned()
                scope=self.scope
                def current(candidate,token):
                    if candidate!=scope or token is not self._token:return False
                    with self._condition:
                        if self._phase!='engine' or self._unresolved or self._completed or not self._active or self._thread!=get_ident():return False
                    self._owned()
                    # Configuration authority remains live after initialization,
                    # including graph-only completion without a provider call.
                    # No registry lock is held across this host callback.
                    self.coordinator._check(int(self.task_id),self.exploration_id,self.policy_revision)
                    self._owned()
                    return True
                def guard(candidate):
                    if candidate!=scope:raise BootstrapConflict('Foreign engine scope')
                    return self.coordinator.guard(int(self.task_id),self.exploration_id)
                child=self._factory(deepcopy(self._task),self.token,scope,current,guard)
                # Retain any actual child even when validation/cancel fails.
                if isinstance(child,TaskEngineHandle):
                    with self._condition:self._child=child
                if not isinstance(child,TaskEngineHandle) or child.scope!=scope or child.token is not self._token or child.store.executor is not self._executor or child.current is not current or child.guard is not guard or child.unresolved or child.wait(0) is not True:raise BootstrapUnresolved('Exact inert engine child required')
                if self._continuation is not None:
                    owner,cap,expected=self._continuation
                    TaskContinuationOwner._transfer_notifications(owner,cap,self,child)
                child.bind_cleanup(self.manager,self)
                child.bind_timing(self.manager,self)
                child.bind_process(self.manager,self)
                if self._continuation is not None:
                    # Prepare the return value before the callback-free final
                    # acknowledgement. Until then both generations are retained.
                    result=EngineProgress('bootstrap_prepared',cancelled=False)
                    TaskContinuationOwner._finish_activation(owner,cap,self,child)
                    return result
                with self._condition:self._phase='engine'
                if self.token.cancelled:child.cancel(self.token.reason)
                else:self._owned()
                return EngineProgress('bootstrap_prepared',cancelled=self.token.cancelled)
            with self._condition:child=self._child
            if child is None:raise BootstrapUnresolved('Engine child missing')
            return child.advance(now)
        except BaseException:
            if initial and self._continuation_pin is not None:
                # Factory/validation/binding failures can happen before the
                # transfer helper runs. Keep its original queue and both owners.
                from .task_continuation import TaskContinuationOwner
                owner,cap,expected=self._continuation_pin
                TaskContinuationOwner.quarantine(owner,cap)
            with self._condition:self._unresolved=True;self._phase='unresolved';child=self._child
            if child is not None:
                try:child.cancel('bootstrap_unresolved')
                except BaseException:pass
            raise BootstrapUnresolved('Bootstrap or engine effects require reconciliation') from None
        finally:
            with self._condition:self._active=False;self._thread=None;self._condition.notify_all()
    def settle_completed(self,complete):
        if not callable(complete):raise ValueError('Completion callback required')
        with self._condition:
            if self._active or self.unresolved or self._completed or self.token.cancelled or self._phase!='engine':return False
            self._owned();child=self._child
            self._active=True;self._thread=get_ident()
        try:
            settled=child.settle_completed(complete)
            if settled is True:
                with self._condition:self._completed=True
            return settled
        except BaseException:
            with self._condition:self._unresolved=True;self._phase='unresolved'
            raise BootstrapUnresolved('Bootstrap engine settlement requires reconciliation') from None
        finally:
            with self._condition:self._active=False;self._thread=None;self._condition.notify_all()
