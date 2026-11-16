"""Opt-in inert post-prepare timing binding; positive runtime gates stay closed.

One original owner supplies one shared clock. Only planner/worker provider
bindings are wrapped; goals and zero-timeout providers are unchanged. This does
not install an observer, final action, listener or recovery/adoption path.
"""
from dataclasses import dataclass
from threading import RLock
from .chat import ChatBinding
from .deadline import OwnedGenerationClock,PreparedDispatchClock
from .task_deadline_provider import DeadlineProvider
from ..db.task_deadline import TaskDeadlineStore

class TimingUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class TaskTimingPolicy:
    revision:str
    clock:object
    wall_clock:object
    stamp_authorize:object
    dispatch_authorize:object
    def __post_init__(self):
        if type(self.revision) is not str or not self.revision.strip() or len(self.revision)>256:
            raise ValueError('Bounded explicit timing policy revision required')
        if any(not callable(v) for v in (self.clock,self.wall_clock,self.stamp_authorize,self.dispatch_authorize)):
            raise ValueError('Explicit timing clocks and grants required')

def _identity(scope):return (scope.task_id,scope.exploration_id,scope.run_id,scope.revision)

class PreparedTaskTiming:
    def __init__(self,scope,policy,*,configuration_current):
        from .engine import EngineScope
        if type(scope) is not EngineScope or type(policy) is not TaskTimingPolicy or not callable(configuration_current):
            raise ValueError('Exact scope, timing policy and configuration authority required')
        self.scope=scope;self.policy=policy;self.configuration_current=configuration_current
        self._pins=(scope,policy,configuration_current,policy.revision,policy.clock,policy.wall_clock,policy.stamp_authorize,policy.dispatch_authorize)
        self._lock=RLock();self._attempted=False;self._bound=None;self.store=None;self.kernel=None;self._resources=None;self._observe=None
    def validate_bindings(self):
        s,p,c,r,mono,wall,stamp,dispatch=self._pins
        if self.scope is not s or self.policy is not p or self.configuration_current is not c or p.revision!=r or any(a is not b for a,b in zip((p.clock,p.wall_clock,p.stamp_authorize,p.dispatch_authorize),(mono,wall,stamp,dispatch))):
            raise TimingUnavailable('Timing policy changed')
        if self._bound is not None:
            manager,outer,engine,receipt,executor,guard,timeout,planner,worker=self._bound
            if (outer.manager is not manager or outer._child is not engine or outer._receipt is not receipt
                or outer.token is not self._token_pin or engine.token is not self._token_pin or engine.scope is not s or outer.executor is not executor
                or manager.repository.executor is not executor or engine.store.executor is not executor
                or outer.coordinator.guard is not guard or outer.prepared_timeout_seconds!=timeout
                or engine.planner is not planner or engine.worker is not worker
                or planner.clock is not mono or worker.clock is not mono or planner._clock is not mono or worker._clock is not mono):raise TimingUnavailable('Prepared timing owner changed')
            if (self.store,self.kernel)!=self._resources:raise TimingUnavailable('Timing resources changed')
            if timeout:
                self.store._binding_check();self.kernel._check()
                if self.kernel.observe!=self._observe:raise TimingUnavailable('Original clock observer changed')
        return True
    def bind(self,manager,outer,engine):
        from ..manager import TaskManager
        from .bootstrap_handle import BootstrapHandle
        from .engine import TaskEngineHandle
        with self._lock:
            self.validate_bindings()
            if self._attempted:raise TimingUnavailable('Timing binding cannot be retried')
            self._attempted=True
            if type(manager) is not TaskManager or type(outer) is not BootstrapHandle or type(engine) is not TaskEngineHandle:
                raise TimingUnavailable('Actual original engine owner required')
            if (outer.manager is not manager or outer._child is not engine or outer.phase!='prepared'
                or outer.scope!=self.scope or engine.scope is not self.scope or outer.token is not engine.token
                or outer.token.cancelled or manager.owns_generation(outer.task_id,outer.token,outer) is not True
                or engine._active or engine._started or engine.wait(0) is not True):raise TimingUnavailable('Inert installed prepared owner required')
            receipt=outer._receipt
            if outer.validate_prepared_receipt(receipt) is not True:raise TimingUnavailable('Original prepared receipt required')
            timeout=outer.prepared_timeout_seconds
            self._token_pin=outer.token
            self._bound=(manager,outer,engine,receipt,outer.executor,outer.coordinator.guard,timeout,engine.planner,engine.worker)
            self._resources=(None,None)
            if timeout:
                self.store=TaskDeadlineStore(outer.executor,manager=manager,handle=outer,receipt=receipt,
                    lifecycle_guard=outer.coordinator.guard,authorize=self._pins[6])
                self.kernel=OwnedGenerationClock(self.store,timeout,monotonic_clock=self._pins[4])
                self._resources=(self.store,self.kernel);self._observe=self.kernel.observe
            self.validate_bindings()
            return True
    def execution_status(self):
        """Read the original shared clock without allocating or stamping it."""
        self.validate_bindings()
        if self._bound is None or self._bound[6]<=0 or self.kernel is None:
            raise TimingUnavailable('Original positive timing binding required')
        observation=self.kernel.observe()
        self.validate_bindings()
        if observation is None:return 'busy'
        if observation.lease is None:return 'unstamped'
        if observation.lease is not self.store.lease:raise TimingUnavailable('Original execution clock changed')
        return 'expired' if observation.expired else 'live'
    def wrap(self,scope,role,binding):
        from .planner import PlannerScope
        from .worker import WorkerScope
        if role not in ('planner','worker') or type(scope) is not (PlannerScope if role=='planner' else WorkerScope):
            raise TimingUnavailable('Exact role-specific runtime scope required')
        self.validate_bindings()
        if self._bound is None or type(binding) is not ChatBinding or role not in ('planner','worker') or _identity(scope)!=_identity(self.scope) or scope.model!=binding.model:
            raise TimingUnavailable('Bound exact planner/worker provider required')
        def current(model):
            self.validate_bindings()
            valid=model==binding.model and self._pins[2](scope) is True
            self.validate_bindings();return valid
        if not current(binding.model):raise TimingUnavailable('Timing configuration revoked')
        if not self._bound[6]:return binding
        facade=PreparedDispatchClock(self.store,model=binding.model,role=role,timeout_seconds=self._bound[6],
            authorize=self._pins[7],binding_current=current,wall_clock=self._pins[5],monotonic_clock=self._pins[4],generation_clock=self.kernel)
        return ChatBinding(binding.model,DeadlineProvider(binding,facade))
