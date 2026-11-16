"""Opt-in authenticated task runtime plus explicit pump ownership.

Composes the component assembly through the existing bootstrap application exactly
once. No listener, schema installation, default credentials/workspace, automatic
resume, provider call, tick or background thread is started. Construction performs
only the existing explicit task inventory load. The caller owns executor lifetime.

Use tick() for a synchronous owned step, or explicitly start_pump()/stop_pump().
Stopping the pump is not task cancellation or successful drain: inspect host.drain
and retain unresolved handles/deadline owners before closing storage. Existing
positive timeout, recovery, live progress and frontend limitations remain intact.
Explicit track_manual_actions creates an inert exact-host registry, not observers,
finalizers or timers. Hosts must register their original manual components before
first use; unregistered components are not automatically discovered or tracked.
"""
from dataclasses import dataclass
import math
from time import monotonic
from ..agent.bootstrap_setup import build_bootstrap_application, BootstrapApplication
from ..agent.pump import EnginePump
from ..manager import RuntimeUnavailable

@dataclass(frozen=True)
class TaskRuntimeApplication:
    runtime: object
    bootstrap: BootstrapApplication
    pump: EnginePump
    read_app: object = None
    manual_actions: object = None
    timed_lifecycle: object = None

    @property
    def app(self):return self.read_app if self.read_app is not None else self.bootstrap.app
    @property
    def host(self):return self.bootstrap.host
    @property
    def manager(self):return self.bootstrap.manager
    @property
    def runner(self):return self.bootstrap.runner
    @property
    def facade(self):return self.bootstrap.facade
    @property
    def admission(self):return self.bootstrap.admission

    def _check(self):
        self.runtime.validate_bindings()
        if self.manual_actions is not None:
            from ..agent.manual_action_owner import ManualActionOwner
            if type(self.manual_actions) is not ManualActionOwner or self.manual_actions.host is not self.host or self.manual_actions._application is not self:
                raise RuntimeUnavailable('Manual action application ownership changed')
            self.manual_actions.validate_bindings()
        if self.timed_lifecycle is not None:
            from ..agent.timed_lifecycle import PreparedTimedLifecycle
            if type(self.timed_lifecycle) is not PreparedTimedLifecycle or self.timed_lifecycle.application is not self:
                raise RuntimeUnavailable('Original timed lifecycle composition required')
            self.timed_lifecycle.validate_bindings()
        if (self.pump.host is not self.host or self.pump.runner is not self.runner
                or self.pump.manager is not self.manager
                or self.admission.host is not self.host
                or self.manager.repository.executor is not self.runtime.executor):
            raise RuntimeUnavailable('Task runtime application ownership changed')
    def tick(self):
        self._check();return self.pump.tick()
    def start_pump(self):
        self._check();return self.pump.start()
    def stop_pump(self,timeout):
        self._check();return self.pump.stop(timeout)


def build_task_runtime_application(runtime, signing_key, password_codec, *,
        live_last_activity, profile_available=None, capacity=1, max_pending=64,
        single_process=False, application_options=None, pump_interval=1.0,
        pump_max_per_tick=8, pump_clock=monotonic, read_services=None, read_router=None,
        track_manual_actions=False, manual_action_capacity=1024, timed_lifecycle_policy=None):
    # Component type import stays local to avoid a setup/component import cycle.
    from ..agent.task_runtime import TaskRuntimeAssembly
    if type(track_manual_actions) is not bool or type(manual_action_capacity) is not int or not 1<=manual_action_capacity<=10000:
        raise ValueError('Explicit manual tracking opt-in and bounded capacity required')
    if type(runtime) is not TaskRuntimeAssembly:
        raise ValueError('Actual task runtime component assembly required')
    if (isinstance(pump_interval,bool) or not isinstance(pump_interval,(int,float))
            or not math.isfinite(pump_interval) or not .01<=pump_interval<=60
            or type(pump_max_per_tick) is not int or not 1<=pump_max_per_tick<=1000
            or not callable(pump_clock)):
        raise ValueError('Explicit bounded pump configuration required')
    if runtime.validate_bindings() is not True:
        raise ValueError('Task runtime bindings unavailable')
    if timed_lifecycle_policy is not None:
        from ..agent.timed_lifecycle import TimedLifecyclePolicy
        from ..agent.engine_runner import EngineRunner
        from ..agent.host import AgentHost
        if type(timed_lifecycle_policy) is not TimedLifecyclePolicy or track_manual_actions is not True:
            raise ValueError('Exact timed lifecycle policy and explicit manual tracking required')
        timed_lifecycle_policy.validate_runtime(runtime)
        if not callable(getattr(EngineRunner,'install_timed_lifecycle',None)) or not callable(getattr(AgentHost,'owns_work',None)):
            raise RuntimeUnavailable('Post-prepare registration capability unavailable')
    if read_router is not None:
        from .task_runtime_read_router import TaskRuntimeReadRouter
        if read_services is not None or (isinstance(application_options,dict) and application_options.get('task_activity_service') is not None):
            raise ValueError('Dynamic reads cannot coexist with fixed task read services')
        if type(read_router) is not TaskRuntimeReadRouter or read_router.tasks.executor is not runtime.executor:
            raise ValueError('Exact dynamic task router/executor required')
        read_router.validate_bindings()
    if read_services is not None:
        from .task_runtime_reads import TaskRuntimeReads
        if type(read_services) is not TaskRuntimeReads or read_services.executor is not runtime.executor:
            raise ValueError('Exact task read composition/executor required')
        read_services.validate_bindings()
    bootstrap=build_bootstrap_application(runtime.executor,signing_key,password_codec,
        coordinator=runtime.coordinator,
        bootstrap_configuration=runtime.bootstrap_configuration,
        engine_factory=runtime.engine_factory,
        initialized_readiness=runtime.initialized_readiness,
        initialized_factory=runtime.initialized_factory,
        live_last_activity=live_last_activity,profile_available=profile_available,
        capacity=capacity,max_pending=max_pending,single_process=single_process,
        application_options=application_options)
    if runtime.validate_bindings() is not True:
        raise RuntimeUnavailable('Task runtime bindings changed during assembly')
    read_app=None if read_services is None else read_services.bind(bootstrap.app)
    if read_router is not None:
        read_router.validate_bindings()
        read_app=read_router.bind(bootstrap.app)
    pump=EnginePump(bootstrap.host,interval=pump_interval,
        max_per_tick=pump_max_per_tick,clock=pump_clock)
    manual=None
    if track_manual_actions:
        from ..agent.manual_action_owner import ManualActionOwner
        manual=ManualActionOwner(bootstrap.host,max_components=manual_action_capacity)
    bundle=TaskRuntimeApplication(runtime,bootstrap,pump,read_app,manual)
    if manual is not None:manual.claim_application(bundle)
    if timed_lifecycle_policy is not None:
        from ..agent.timed_lifecycle import PreparedTimedLifecycle
        lifecycle=PreparedTimedLifecycle(bundle,timed_lifecycle_policy)
        # Complete the one-shot self-owned bundle before exposing it to callers.
        object.__setattr__(bundle,'timed_lifecycle',lifecycle)
        lifecycle.validate_bindings()
        bundle.runner.install_timed_lifecycle(lifecycle)
    bundle._check()
    return bundle
