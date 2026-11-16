"""Explicit per-prepared action composition on the existing runtime application.

No positive admission, schema installation, model call, clock stamp or action tick
is performed here. Missing final configuration is explicit and never admission
ready. Policies are host-supplied authority, not inferred from persisted flags.
"""
from dataclasses import dataclass,fields
from threading import Lock
import hashlib
from .task_timing import TaskTimingPolicy,PreparedTaskTiming
from .owned_finalization import FinalizationPlan,OwnedFinalization
from .expiry_observer import OwnedExpiryObserver
from .frontier_completion import FrontierCompletionPolicy,OwnedFrontierCompletion
from ..db.early_completion import EarlyCompletionGrant

class TimedLifecycleUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class TimedLifecyclePolicy:
    revision:str
    timing_policy:object
    issue_frontier_grant:object
    frontier_authorize:object
    observation_authorize:object
    freeze_authorize:object
    final_plan:object=None
    def __post_init__(self):
        if type(self.revision) is not str or not self.revision.strip() or len(self.revision)>256 or type(self.timing_policy) is not TaskTimingPolicy:raise ValueError('Explicit bounded timed lifecycle policy required')
        self.timing_policy.__post_init__()
        if any(not callable(v) for v in (self.issue_frontier_grant,self.frontier_authorize,self.observation_authorize,self.freeze_authorize)):raise ValueError('Explicit lifecycle grant callbacks required')
        if self.final_plan is not None:
            if type(self.final_plan) is not FinalizationPlan:raise ValueError('Exact explicit final plan required')
            self.final_plan.__post_init__()
    def validate_runtime(self,runtime):
        from .task_runtime import TaskRuntimeAssembly
        self.__post_init__()
        if type(runtime) is not TaskRuntimeAssembly or runtime._timing_policy is not self.timing_policy or runtime._timing_pin is not self.timing_policy or runtime._cleanup_authorize is None:raise ValueError('Same actual timing policy and cleanup runtime required')
        if self.final_plan is not None and self.final_plan.transcript is not runtime.transcript:raise ValueError('Original canonical transcript required for final plan')
        if self.final_plan is not None and self.final_plan.task_provider is not None:
            from .final_task_provider import FinalTaskProviderAdapter
            adapter=self.final_plan.task_provider
            if type(adapter) is not FinalTaskProviderAdapter or adapter.resolver is not runtime.providers:raise ValueError('Original runtime persisted final provider required')
            adapter.validate_bindings()
        runtime.validate_bindings();return True

@dataclass(frozen=True)
class PreparedTimedActions:
    handle:object
    receipt:object
    timing:object
    observer:object
    frontier:object
    finalizer:object=None

@dataclass(frozen=True)
class TimedLifecycleStatus:
    registered:int
    failed:int
    finalization_configured:bool
    admission_ready:bool=False

@dataclass
class _Entry:
    handle:object
    token:object
    receipt:object
    timing:object
    products:list
    actions:object=None
    failed:bool=False

class PreparedTimedLifecycle:
    def __init__(self,application,policy):
        from ..server.task_runtime_setup import TaskRuntimeApplication
        from .manual_action_owner import ManualActionOwner
        if type(application) is not TaskRuntimeApplication or type(policy) is not TimedLifecyclePolicy or type(application.manual_actions) is not ManualActionOwner:raise ValueError('Existing tracked runtime application and exact policy required')
        policy.validate_runtime(application.runtime)
        if application.manual_actions.host is not application.host:raise ValueError('Original host tracker required')
        self.application=application;self.policy=policy
        self._pins=(application,application.runtime,application.host,application.manager,application.runner,application.manual_actions,application.runtime.executor,policy)
        self._fields=tuple(getattr(policy,f.name) for f in fields(policy))
        self._final_fields=None if policy.final_plan is None else tuple(getattr(policy.final_plan,f.name) for f in fields(policy.final_plan))
        self._final_digest=None if policy.final_plan is None else policy.final_plan.configuration.digest
        self._lock=Lock();self._entries={};self._active=False
        self._owns_work=getattr(application.host,'owns_work',None)
        self._bind_frontier=application.runner.bind_frontier_completion
        self._register_methods=(application.manual_actions.register_observer,application.manual_actions.register_frontier_completion,application.manual_actions.register_finalizer)
    def validate_bindings(self):
        app,runtime,host,manager,runner,manual,executor,policy=self._pins
        if self.application is not app or self.policy is not policy or app.runtime is not runtime or app.host is not host or app.manager is not manager or app.runner is not runner or app.manual_actions is not manual or app.timed_lifecycle is not self or runtime.executor is not executor or manager.repository.executor is not executor or manual.host is not host:
            raise TimedLifecycleUnavailable('Timed application ownership changed')
        if any(getattr(policy,f.name) is not v for f,v in zip(fields(policy),self._fields)):raise TimedLifecycleUnavailable('Lifecycle policy changed')
        if policy.final_plan is not None and (policy.final_plan.configuration.digest!=self._final_digest or any(getattr(policy.final_plan,f.name) is not v for f,v in zip(fields(policy.final_plan),self._final_fields))):raise TimedLifecycleUnavailable('Final policy changed')
        if (self._owns_work is None or host.owns_work!=self._owns_work or runner.bind_frontier_completion!=self._bind_frontier
            or manual._application is not app or (manual.register_observer,manual.register_frontier_completion,manual.register_finalizer)!=self._register_methods):raise TimedLifecycleUnavailable('Original host registration slot unavailable')
        policy.validate_runtime(runtime);manual.validate_bindings();return True
    def _owner(self,handle,timing=None):
        from .bootstrap_handle import BootstrapHandle
        self.validate_bindings();app,runtime,host,manager,runner,manual,executor,policy=self._pins
        if type(handle) is not BootstrapHandle or handle.manager is not manager or handle.executor is not executor or handle.token.cancelled or handle.phase!='engine' or handle.unresolved or handle.wait(0) is not True or manager.owns_generation(handle.task_id,handle.token,handle) is not True:
            raise TimedLifecycleUnavailable('Idle installed prepared generation required')
        if self._owns_work(runner) is not True or not any(h is handle for h in runner.handles()):raise TimedLifecycleUnavailable('Original owned-work registration slot required')
        r=handle._receipt
        if handle.validate_prepared_receipt(r) is not True:raise TimedLifecycleUnavailable('Exact prepared receipt required')
        if timing is not None:
            if type(timing) is not PreparedTaskTiming or handle._child.timing is not timing or timing.policy is not policy.timing_policy or timing._bound is None or timing._bound[1] is not handle or timing._bound[3] is not r:raise TimedLifecycleUnavailable('Original prepared timing required')
            timing.validate_bindings()
        handle.coordinator._check(r.task_id,r.exploration_id,handle.policy_revision)
        self.validate_bindings()
        if handle.validate_prepared_receipt(r) is not True or not manager.owns_generation(handle.task_id,handle.token,handle) or self._owns_work(runner) is not True:raise TimedLifecycleUnavailable('Registration ownership changed')
        return r
    def register_prepared(self,handle):
        self._owner(handle)
        if handle.prepared_timeout_seconds==0:return None
        timing=handle._child.timing;r=self._owner(handle,timing)
        if timing.store is None or timing.kernel is None or timing.store.lease is not None:raise TimedLifecycleUnavailable('Fresh positive prepared clock resources required')
        with self._lock:
            if self._active or handle in self._entries or len(self._entries)>=10000:raise TimedLifecycleUnavailable('Timed registration cannot replay or reenter')
            self._active=True;entry=_Entry(handle,handle.token,r,timing,[]);self._entries[handle]=entry
        try:
            policy=self._pins[7];manual=self._pins[5]
            grant=policy.issue_frontier_grant(r)
            if type(grant) is EarlyCompletionGrant:entry.products.append(grant)
            self._owner(handle,timing)
            if type(grant) is not EarlyCompletionGrant:raise TimedLifecycleUnavailable('Actual scoped frontier permit required')
            grant._validate(r,'reserve')
            observer=OwnedExpiryObserver(self._pins[2],timing.kernel,authorize_observation=policy.observation_authorize,authorize_freeze=policy.freeze_authorize)
            entry.products.append(observer);self._register_methods[0](observer);self._owner(handle,timing)
            frontier=OwnedFrontierCompletion(self._pins[2],handle,timing.store,
                FrontierCompletionPolicy(int.from_bytes(hashlib.sha256(policy.revision.encode()).digest()[:8],'big') or 1,grant,policy.frontier_authorize),manual)
            entry.products.append(frontier);self._register_methods[1](frontier);self._owner(handle,timing)
            finalizer=None
            if policy.final_plan is not None:
                finalizer=OwnedFinalization(observer,policy.final_plan);entry.products.append(finalizer)
                self._register_methods[2](finalizer);self._owner(handle,timing)
            self._bind_frontier(handle,frontier);self._owner(handle,timing)
            actions=PreparedTimedActions(handle,r,timing,observer,frontier,finalizer)
            with self._lock:entry.actions=actions
            return actions
        except BaseException:
            with self._lock:entry.failed=True
            raise TimedLifecycleUnavailable('Prepared action registration retained for reconciliation') from None
        finally:
            with self._lock:self._active=False
    def failed(self,handle):
        with self._lock:
            entry=self._entries.get(handle)
            return entry is not None and entry.failed
    def registered(self,handle):
        with self._lock:
            entry=self._entries.get(handle)
            return entry is not None and not entry.failed and entry.actions is not None
    def actions(self,handle):
        self.validate_bindings()
        with self._lock:
            entry=self._entries.get(handle)
            if entry is None or entry.failed or entry.actions is None or entry.handle is not handle or entry.token is not handle.token:raise TimedLifecycleUnavailable('Original registered actions unavailable')
            return entry.actions
    def status(self):
        self.validate_bindings()
        with self._lock:return TimedLifecycleStatus(sum(e.actions is not None and not e.failed for e in self._entries.values()),sum(e.failed for e in self._entries.values()),self._pins[7].final_plan is not None,False)
