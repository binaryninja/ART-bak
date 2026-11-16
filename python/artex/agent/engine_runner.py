"""Opt-in synchronous runner for already initialized single-process task graphs.

readiness(task) is a read-only host validator returning the current durable
EngineScope, including persisted run identity and policy revision. It must reject
unsupported lifecycle/profile/deadline/bootstrap choices before manager admission.
factory(task, token, scope) only constructs a TaskEngineHandle: no model calls,
threads, tools, graph mutations or unowned work are permitted in either callback.
The host owns these trusted callbacks; runtime introspection cannot prove purity.

Optional initialization_readiness(task) selects bootstrap with literal True or
initialized mode with literal False. Provisional creation requires True. It must
read-validate journal installation, profiles, prompts, policy, transcript storage
and inherited-source absence (persisted snapshots do not contain source edges).
bootstrap_factory(task, token, claimed_manager) constructs an inert actual
BootstrapHandle. Both callbacks are explicit, trusted, and may neither write nor
invoke a provider. Only first owned advance allocates a journal run. No seed,
background tick, recovery, scheduler or default app
wiring. Explicit advance may execute one bounded child synchronously. An idle or
drained handle is NOT completed. Only the engine's fresh persisted-goal settlement
may call manager.complete. Unresolved handles remain owned even after cancel.
Hosted cancelled-handle retirement requires the manager's exact acknowledgement;
forgetting an execution attachment never releases a retained clock owner.
All admission/settlement must go through AgentHost; graph writers share its
engine lifecycle authority. This adapter itself grants no model/tool permission.
"""
from copy import deepcopy
from collections.abc import Mapping
from threading import RLock
from .engine import EngineScope, TaskEngineHandle
from ..manager import CancellationToken, RuntimeUnavailable, TaskConflict
from ..db.core import int64
from .bootstrap_handle import BootstrapHandle


class EngineRunner:
    def __init__(self, *, readiness, factory, max_pending=64, initialization_readiness=None, bootstrap_factory=None):
        if not callable(readiness) or not callable(factory):
            raise ValueError('Explicit pure readiness and construction callbacks required')
        if type(max_pending) is not int or not 1 <= max_pending <= 10000:
            raise ValueError('Invalid runner capacity')
        if (initialization_readiness is None) != (bootstrap_factory is None) or (initialization_readiness is not None and (not callable(initialization_readiness) or not callable(bootstrap_factory))):
            raise ValueError('Initialization requires both explicit read-only readiness and inert factory')
        self._initialization_readiness=initialization_readiness
        self._bootstrap_factory=bootstrap_factory
        self._readiness,self._factory=readiness,factory
        self.max_pending=max_pending
        self._continuation_owner=None
        self._lock=RLock(); self._handles={}; self._host=None; self._host_executor=None; self._host_manager=None
        self._frontier={};self._final_retirement={};self._host_settlement_check=None
        self._intent_control=None;self._intent_methods=None
        self._timed_admission=None;self._admission_methods=None
        self._timed_lifecycle=None;self._timed_methods=None;self._timed_attempts={};self._host_work_check=None

    def claim_host(self, host):
        """One lifecycle fence for this runner, retained even on setup failure.

        Idempotent only for the exact same host. No automatic release/adoption
        after uncertainty; an unused failed assembly needs a fresh runner.
        """
        from .host import AgentHost
        if not isinstance(host,AgentHost) or host.runner is not self or host.manager.runner is not self:
            raise RuntimeUnavailable('Exact AgentHost identity required')
        from ..db.tasks import TaskStore
        if not isinstance(host.manager.repository,TaskStore):
            raise RuntimeUnavailable('Hosted engine requires an actual task repository executor')
        executor=host.manager.repository.executor
        with self._lock:
            if self._host is host:
                if executor is not self._host_executor or host.manager is not self._host_manager:
                    raise RuntimeUnavailable('Hosted manager or database identity changed')
                return
            if self._host is not None or self._handles:
                raise RuntimeUnavailable('Engine runner already has lifecycle ownership')
            self._host=host
            self._host_settlement_check=host.owns_settlement
            self._host_work_check=host.owns_work
            self._host_executor=executor
            self._host_manager=host.manager

    def install_intent_control(self,adapter):
        """One explicit typed control adapter; no routes or automatic action."""
        from .intent_control import IntentControl
        if type(adapter) is not IntentControl or adapter.host is not self._host or self._host is None:
            raise RuntimeUnavailable('Exact original host intent adapter required')
        names=('validate_bindings','execute','inspect','retained','bind_frozen_inspection')
        methods=tuple(getattr(adapter,n) for n in names)
        if any(getattr(m,'__func__',None) is not getattr(IntentControl,n) for n,m in zip(names,methods)):
            raise RuntimeUnavailable('Original intent control methods required')
        methods[0]()
        with self._lock:
            if self._intent_control is not None:raise TaskConflict('Intent control already installed')
            self._intent_control=adapter;self._intent_methods=methods

    def _intent_adapter(self):
        adapter=self._intent_control
        if adapter is None:return None
        if tuple(getattr(adapter,n) for n in ('validate_bindings','execute','inspect','retained','bind_frozen_inspection'))!=self._intent_methods:
            raise RuntimeUnavailable('Intent control methods changed')
        self._intent_methods[0]();return adapter

    def _intent_retained(self,handle):
        adapter=self._intent_adapter()
        if adapter is None:return False
        result=self._intent_methods[3](handle)
        if type(result) is not bool:raise RuntimeUnavailable('Exact intent retention result required')
        return result

    def control_intent(self,task_id,intent_id,action,*,timeout=5.0):
        from .host import AgentHost
        if self._host is None or getattr(self._host.owns_intent_control,'__func__',None) is not AgentHost.owns_intent_control or not AgentHost.owns_intent_control(self._host,self,task_id,intent_id,action):
            raise TaskConflict('Original hosted intent control slot required')
        adapter=self._intent_adapter()
        if adapter is None:raise RuntimeUnavailable('Explicit intent control unavailable')
        key=str(task_id)
        with self._lock:handle=self._handles.get(key)
        if type(handle) is not BootstrapHandle or handle.manager is not self._host_manager or handle.phase!='engine' or type(handle._child) is not TaskEngineHandle:
            raise RuntimeUnavailable('Actual prepared local generation required')
        if not self._host_manager.owns_generation(key,handle.token,handle):
            if action!='inspect' or not self._intent_retained(handle):
                raise TaskConflict('Original running generation required')
            # Only a pre-existing pending pause can transfer to its original
            # registered observer's frozen cleanup authority. Never new work.
            from .expiry_observer import OwnedExpiryObserver
            from ..manager import TaskManager
            binder=self._timed_binding()
            if binder is None:raise TaskConflict('Original registered expiry owner required')
            actions=binder.actions(handle);observer=actions.observer
            if (actions.handle is not handle or type(observer) is not OwnedExpiryObserver
                or observer.host is not self._host or actions.timing.kernel is not observer.clock
                or observer.clock.store.handle is not handle or observer.clock.store.manager is not self._host_manager
                or observer._lease is None or observer._freeze is None or observer._transfer is not None
                or observer.clock.store.lease is not observer._lease
                or getattr(self._host_manager.validate_deadline_observation,'__func__',None) is not TaskManager.validate_deadline_observation):
                raise TaskConflict('Exact pending freeze inspection required')
            TaskManager.validate_deadline_observation(self._host_manager,key,handle.token,handle,
                observer._lease,observer._freeze)
            self._intent_methods[4](handle,intent_id,observer)
        method=self._intent_methods[2] if action=='inspect' else self._intent_methods[1]
        result=method(handle,intent_id,timeout) if action=='inspect' else method(handle,intent_id,action,timeout)
        self._intent_adapter()
        with self._lock:
            if self._handles.get(key) is not handle:raise TaskConflict('Intent generation changed')
        return result

    def install_timed_admission(self,cap):
        """Explicit verified capability, only after original app composition."""
        from .timed_admission import PreparedTimedAdmission
        if type(cap) is not PreparedTimedAdmission:raise RuntimeUnavailable('Exact timed admission required')
        names=('validate_bindings','validate_task','validate_handle_configuration','validate_admission_open')
        methods=tuple(getattr(cap,n) for n in names)
        if any(getattr(m,'__func__',None) is not getattr(PreparedTimedAdmission,n) for n,m in zip(names,methods)):
            raise RuntimeUnavailable('Original admission methods required')
        cap.validate_admission_open()
        app=cap._application
        if (self._host is None or app.runner is not self or app.host is not self._host
            or app.manager is not self._host_manager or app.runtime.executor is not self._host_executor
            or app.timed_lifecycle is not self._timed_lifecycle or self._timed_lifecycle is None):
            raise RuntimeUnavailable('Original fully composed timed application required')
        with self._lock:
            if self._timed_admission is not None or self._handles:raise TaskConflict('Timed admission requires unused runner')
            self._timed_admission=cap;self._admission_methods=methods

    def _admission_cap(self):
        cap=self._timed_admission
        if cap is None:raise RuntimeUnavailable('Positive timeout requires explicit verified admission')
        names=('validate_bindings','validate_task','validate_handle_configuration','validate_admission_open')
        if tuple(getattr(cap,n) for n in names)!=self._admission_methods:
            raise RuntimeUnavailable('Timed admission methods changed')
        # Admission-only fence; existing handles retain structural cleanup access.
        self._admission_methods[3]()
        return cap

    def timed_admission_for(self,task,*,policy_revision):
        if task.get('timeout_seconds',0)==0:return None
        cap=self._admission_cap()
        cap.validate_task(deepcopy(task),runner=self)
        if cap.runtime.bootstrap_configuration(deepcopy(task)).policy_revision!=policy_revision:
            raise RuntimeUnavailable('Timed admission configuration changed')
        self._admission_cap();return cap

    def validate_create(self, description, goal, options, seed_first_intent=False):
        if self._initialization_readiness is None:
            raise RuntimeUnavailable('Engine runner requires an initialized persisted graph; creation/bootstrap unavailable')
        self._prepare({'provisional':True,'description':description,'goal':goal,
                       'options':deepcopy(options),'seed_first_intent':seed_first_intent})

    def _prepare(self, task):
        if not isinstance(task,Mapping):raise RuntimeUnavailable('Task snapshot required')
        task=deepcopy(dict(task))
        bootstrap=False;positive=False
        if self._initialization_readiness is not None:
            if self._host is None:raise RuntimeUnavailable('Initialization requires a claimed persisted host')
            self.claim_host(self._host)
            options=task.get('options')
            if options is None:options={}
            elif not isinstance(options,Mapping):
                from ..db.tasks import TaskCreateOptions
                from dataclasses import asdict
                if not isinstance(options,TaskCreateOptions):raise RuntimeUnavailable('Typed task options required')
                options=asdict(options)
            if type(task.get('seed_first_intent',False)) is not bool or task.get('seed_first_intent'):
                raise RuntimeUnavailable('Intent seeding unavailable')
            for source in (task,options):
                if source.get('deadline_at') or source.get('source_task_ids') or source.get('parent_ref') or source.get('intercept_rules'):
                    raise RuntimeUnavailable('Inherited context, existing deadline and intercept bootstrap unavailable')
                positive=positive or source.get('timeout_seconds',0)!=0
            if positive:
                cap=self._admission_cap();cap.validate_task(deepcopy(task),runner=self);self._admission_cap()
            bootstrap=self._initialization_readiness(deepcopy(task))
            self.claim_host(self._host)
            if positive:self._admission_cap()
            if type(bootstrap) is not bool:raise RuntimeUnavailable('Initialization readiness must return literal bool')
            if positive and not bootstrap:raise RuntimeUnavailable('Positive timeout requires fresh bootstrap')
            if task.get('provisional'):
                if not bootstrap:raise RuntimeUnavailable('Creation requires initialization readiness')
                return task,None
        if task.get('provisional') or task.get('seed_first_intent') or (not bootstrap and (task.get('admission_mode')=='bootstrap' or task.get('queue_mode')=='bootstrap')):
            raise RuntimeUnavailable('Engine bootstrap and intent seeding unavailable')
        try:
            raw_id=task.get('id')
            if isinstance(raw_id,str) and raw_id.isascii() and raw_id.isdecimal():raw_id=int(raw_id)
            task_id=int64(raw_id,'task ID')
            exploration_id=int64(task.get('exploration_id'),'exploration ID')
        except (TypeError,ValueError):raise RuntimeUnavailable('Initialized task identity required') from None
        if task_id<=0 or exploration_id<=0 or (task.get('timeout_seconds',0)!=0 and not (positive and bootstrap)):
            raise RuntimeUnavailable('Initialized graph without deadline required')
        if bootstrap:return task,None
        scope=self._readiness(deepcopy(task))
        if not isinstance(scope,EngineScope) or scope.task_id!=task_id or scope.exploration_id!=exploration_id:
            raise RuntimeUnavailable('Readiness must supply exact durable engine scope')
        if type(scope.run_id) is not int or not 0<scope.run_id<2**63 or not isinstance(scope.revision,str) or not scope.revision:
            raise RuntimeUnavailable('Durable run and revision identity required')
        return task,scope

    def ready(self, task):
        if self._host is not None:self.claim_host(self._host)
        task,scope=self._prepare(task)
        key=None if task.get('provisional') else str(int(task['id']))
        with self._lock:
            if key in self._frontier:raise TaskConflict('Frontier generation still owns retirement')
            old=self._handles.get(key)
            if old is not None and (old.unresolved or not old.token.cancelled or not old.wait(0)):
                raise TaskConflict('Prior engine generation still owns work')
            if len(self._handles)>=self.max_pending and key not in self._handles:
                raise RuntimeUnavailable('Engine capacity exhausted')
        return True

    def install_timed_lifecycle(self, binder):
        """Install one exact existing-application binder before any admission."""
        from .timed_lifecycle import PreparedTimedLifecycle
        if type(binder) is not PreparedTimedLifecycle or self._host is None:
            raise RuntimeUnavailable('Exact hosted timed lifecycle required')
        binder.validate_bindings()
        app=binder.application
        if app.runner is not self or app.host is not self._host or app.manager is not self._host_manager or app.runtime.executor is not self._host_executor:
            raise RuntimeUnavailable('Original timed application required')
        names=('validate_bindings','register_prepared','failed','registered','actions')
        methods=tuple(getattr(binder,n) for n in names)
        if any(getattr(m,'__func__',None) is not getattr(PreparedTimedLifecycle,n) for n,m in zip(names,methods)):
            raise RuntimeUnavailable('Original lifecycle methods required')
        with self._lock:
            if self._timed_lifecycle is not None or self._handles:
                raise TaskConflict('Timed lifecycle installation requires unused runner')
            self._timed_lifecycle=binder;self._timed_methods=methods

    def _timed_binding(self):
        binder=self._timed_lifecycle
        if binder is None:return None
        names=('validate_bindings','register_prepared','failed','registered','actions')
        if tuple(getattr(binder,n) for n in names)!=self._timed_methods:
            raise RuntimeUnavailable('Timed lifecycle methods changed')
        self._timed_methods[0]()
        return binder

    def _timed_blocked(self,handle):
        binder=self._timed_binding()
        if binder is None:return False
        with self._lock:state=self._timed_attempts.get(handle)
        if state in ('registering','failed'):return True
        if isinstance(handle,BootstrapHandle) and handle.scope is not None and handle.prepared_timeout_seconds>0:
            return state!='registered' or binder.failed(handle) or not binder.registered(handle)
        return False

    def bind_frontier_completion(self, handle, component):
        """Explicit prepared timed generation only; no default policy/admission.

        Binding performs pure original-resource validation, never SQL, grants or
        provider execution. Controller tracking owns any later removal tail.
        """
        from .frontier_completion import OwnedFrontierCompletion
        from .manual_action_owner import ManualActionOwner
        if type(handle) is not BootstrapHandle or type(component) is not OwnedFrontierCompletion:
            raise RuntimeUnavailable('Exact prepared frontier components required')
        if self._host is None:raise RuntimeUnavailable('Frontier requires original hosted runner')
        self.claim_host(self._host)
        for name in ('run_once','retirement_result','confirm_runner_removal'):
            if getattr(getattr(component,name),'__func__',None) is not getattr(OwnedFrontierCompletion,name):
                raise RuntimeUnavailable('Original frontier methods required')
        component._binding()
        owner=component._manual_owner_pin
        if (type(owner) is not ManualActionOwner or owner.host is not self._host
            or component.host is not self._host or component.handle is not handle
            or component.deadline.receipt!=handle._receipt
            or component.deadline.executor is not self._host_executor
            or handle.manager is not self._host_manager or handle.token.cancelled
            or type(handle.prepared_timeout_seconds) is not int or handle.prepared_timeout_seconds<=0
            or component._used or component._active or component._failed):
            raise RuntimeUnavailable('Original unused positive frontier required')
        with owner._condition:
            if owner._sealed or owner._components.get(component)!='frontier':
                raise RuntimeUnavailable('Registered unsealed frontier owner required')
        if handle.validate_prepared_receipt(component.deadline.receipt) is not True:
            raise RuntimeUnavailable('Original prepared receipt required')
        if self._host_manager.owns_generation(handle.task_id,handle.token,handle) is not True:
            raise RuntimeUnavailable('Original installed generation required')
        entry=(handle,component,handle.token,component.run_once,component.retirement_result,
               component.confirm_runner_removal,handle.wait)
        with self._lock:
            if self._handles.get(handle.task_id) is not handle or handle.task_id in self._frontier:
                raise TaskConflict('Frontier binding cannot adopt or replace an inventory entry')
            self._frontier[handle.task_id]=entry

    def _frontier_slot(self):
        host=self._host
        if (host is None or host.owns_settlement!=self._host_settlement_check
            or self._host_settlement_check(self) is not True):
            raise TaskConflict('Frontier completion requires original host settlement slot')

    def _frontier_expiry_owned(self,handle,component):
        """Defer unused frontier only to its original registered expiry owner.

        This is retained ownership, not completion, drain, or error suppression.
        Unknown/quarantined generations still fail the exact manager predicates.
        """
        binder=self._timed_binding()
        if binder is None:return False
        from .expiry_observer import OwnedExpiryObserver
        from .owned_finalization import OwnedFinalization,FinalizationHandoff
        from ..manager import TaskManager
        actions=binder.actions(handle)
        observer=actions.observer;manager=self._host_manager
        if (actions.handle is not handle or actions.frontier is not component
            or type(observer) is not OwnedExpiryObserver or observer.host is not self._host
            or observer.clock.store is not component.deadline
            or actions.timing.kernel is not observer.clock):
            raise RuntimeUnavailable('Original frontier expiry registration changed')
        # No lease/freeze means the original frontier branch still owns the choice.
        if observer._freeze is None:return False
        for name in ('validate_deadline_observation','validate_deadline_assessment'):
            if getattr(getattr(manager,name),'__func__',None) is not getattr(TaskManager,name):
                raise RuntimeUnavailable('Original expiry manager predicates required')
        OwnedExpiryObserver._check(observer)
        lease=observer._lease;freeze=observer._freeze
        if lease is None or component.deadline.lease is not lease:
            raise RuntimeUnavailable('Original frozen lease required')
        handoff=observer._transfer
        if handoff is None:
            TaskManager.validate_deadline_observation(manager,handle.task_id,handle.token,handle,lease,freeze)
            return True
        finalizer=actions.finalizer
        if (type(handoff) is not FinalizationHandoff or type(finalizer) is not OwnedFinalization
            or handoff.owner is not finalizer or handoff.observer is not observer
            or handoff.freeze is not freeze or handoff.lease is not lease
            or observer._final_owner is not finalizer or finalizer.observer is not observer
            or finalizer._failed):
            raise RuntimeUnavailable('Original final handoff required')
        OwnedFinalization._validate(finalizer)
        if TaskManager.validate_deadline_assessment(manager,handoff.assessment,freeze,
                handle.token,handle,lease,finalizer.plan.configuration.digest,inspection=False) is not True:
            raise RuntimeUnavailable('Original final assessment ownership required')
        return True

    def _settle_final_frontier(self,key,entry):
        """Consume only original final settlement evidence, retaining ack retries."""
        handle,component,token,run,early_retirement,early_confirm,wait=entry
        with self._lock:owned=self._final_retirement.get(handle)
        if owned is None:return None
        binder,actions,finalizer,methods=owned
        if (self._timed_binding() is not binder or binder.actions(handle) is not actions
            or actions.finalizer is not finalizer or actions.frontier is not component
            or tuple(getattr(finalizer,n) for n in ('bind_runner_retirement','retirement_result','confirm_runner_removal'))!=methods):
            raise RuntimeUnavailable('Original final retirement binding changed')
        if finalizer.result is None:return None
        # The final invocation tail still owns resources even after durable commit.
        finalizer._retirement_pins()
        if finalizer._active:return False
        retirement=methods[1];confirm=methods[2]
        receipt=retirement(self)
        if wait(0) is not True or handle.unresolved is not False:
            raise TaskConflict('Final execution effects have not drained')
        self._frontier_slot()
        if (retirement(self) is not receipt or handle.wait!=wait
            or tuple(getattr(finalizer,n) for n in ('bind_runner_retirement','retirement_result','confirm_runner_removal'))!=methods):
            raise RuntimeUnavailable('Final retirement proof changed')
        with self._lock:
            if self._frontier.get(key) is not entry or self._final_retirement.get(handle) is not owned:
                raise TaskConflict('Final retirement inventory changed')
            current=self._handles.get(key)
            if current is handle:del self._handles[key]
            elif current is not None:raise TaskConflict('Final retirement generation changed')
        # Keep both ledgers until read-only confirmation succeeds, including seal.
        if confirm() is not True:raise TaskConflict('Final removal confirmation pending')
        self._frontier_slot()
        with self._lock:
            if self._frontier.get(key) is not entry or self._final_retirement.get(handle) is not owned:
                raise TaskConflict('Final retirement inventory changed')
            del self._frontier[key];del self._final_retirement[handle]
        return True

    def _settle_frontier(self,key,entry):
        self._frontier_slot()
        handle,component,token,run,retirement,confirm,wait=entry
        if (component.handle is not handle or handle.token is not token or component.run_once!=run
            or component.retirement_result!=retirement or component.confirm_runner_removal!=confirm
            or handle.wait!=wait):raise RuntimeUnavailable('Frontier completion binding changed')
        component._binding()
        with self._lock:
            if self._frontier.get(key) is not entry:raise TaskConflict('Frontier binding changed')
            installed=self._handles.get(key)
        if installed is not None and installed is not handle:raise TaskConflict('Frontier inventory generation changed')
        # A used controller must never replay SQL. Its private evidence accessor
        # distinguishes committed retirement from attempted/uncertain effects.
        if not component._used:
            final=self._settle_final_frontier(key,entry)
            if final is not None:return final
            if installed is not handle:raise TaskConflict('Frontier handle missing before attempt')
            if self._frontier_expiry_owned(handle,component):return False
            result=run()
            if result.phase in ('busy','waiting','expiry_required'):return False
            if result.phase!='awaiting_runner_removal':raise RuntimeUnavailable('Unexpected frontier outcome')
        receipt=retirement(self)
        if wait(0) is not True or handle.unresolved is not False:
            raise TaskConflict('Frontier effects have not drained')
        self._frontier_slot()
        # Wait can execute callbacks; revalidate exact private retirement proof
        # and original methods before a callback-free compare/remove.
        if (retirement(self) is not receipt or component.retirement_result!=retirement or handle.wait!=wait
            or component.run_once!=run or component.confirm_runner_removal!=confirm):
            raise RuntimeUnavailable('Frontier retirement proof changed')
        with self._lock:
            if self._frontier.get(key) is not entry:raise TaskConflict('Frontier binding changed')
            current=self._handles.get(key)
            if current is handle:del self._handles[key]
            elif current is not None:raise TaskConflict('Frontier inventory generation changed')
        # Keep binding on failure: sealed ManualActionOwner permits this exact
        # already-retired read-only continuation, never another store attempt.
        if confirm() is not True:raise TaskConflict('Frontier removal confirmation pending')
        self._frontier_slot()
        with self._lock:
            if self._frontier.get(key) is not entry:raise TaskConflict('Frontier binding changed')
            del self._frontier[key]
            self._final_retirement.pop(handle,None)
        return True

    def install_continuation(self,owner):
        from .task_continuation import TaskContinuationOwner
        if type(owner) is not TaskContinuationOwner or self._host is None or owner.runner is not self or owner.host is not self._host or owner.manager is not self._host_manager:raise RuntimeUnavailable('Exact continuation owner required')
        names=('reserve_pause','confirm_pause','prepare_resume','commit_resume','confirm_installed','quarantine','retained','seal_resume','wait_resumes','_begin_resume','_end_resume','_resume_open','validate_bindings')
        methods=tuple(getattr(owner,n) for n in names)
        if any(getattr(m,'__self__',None) is not owner or getattr(m,'__func__',None) is not getattr(TaskContinuationOwner,n) for n,m in zip(names,methods)):raise RuntimeUnavailable('Original continuation methods required')
        owner.validate_bindings();self.claim_host(self._host)
        with self._lock:
            if self._continuation_owner is not None:
                if self._continuation_owner is owner:return False
                raise TaskConflict('Continuation owner already installed')
            self._continuation_owner=owner
        with self._host_manager._lock:
            if self._host_manager._continuation_owner is not None and self._host_manager._continuation_owner is not owner:raise TaskConflict('Manager continuation owner changed')
            self._host_manager._continuation_owner=owner
            self._host_manager._continuation_runner=self
            self._host_manager._continuation_methods=(names,methods)
        return True

    def install_resumed_handle(self,cap,handle):
        owner=self._continuation_owner
        if owner is None or cap.owner is not owner:raise RuntimeUnavailable('Original continuation required')
        self._host_manager._continuation_binding(owner);self.claim_host(self._host)
        if type(handle) is not BootstrapHandle or handle.manager is not self._host_manager or handle.executor is not self._host_executor or handle.scope is not None or handle.phase!='installed' or handle.token.cancelled or handle.unresolved or handle.wait(0) is not True:raise RuntimeUnavailable('Exact inert continuation required')
        handle.validate_construction()
        old=self._host_manager._continuation_installation(cap,handle,handle.token)
        if not old.token.cancelled or old.unresolved or old.wait(0) is not True:raise TaskConflict('Previous generation not drained')
        owner.validate_bindings()
        if self._host_manager._continuation_installation(cap,handle,handle.token) is not old:raise TaskConflict('Suspension changed')
        with self._lock:
            key=handle.task_id
            if self._handles.get(key) is not old or key in self._frontier:raise TaskConflict('Original runner ownership changed')
            # Atomic with irreversible sealing. No policy/SQL under these locks.
            with owner._lock:
                if owner._resume_sealed:raise RuntimeUnavailable('Resume admission sealed')
                self._handles[key]=handle
        return True

    def _retire_cancelled(self, key, handle):
        """Detach execution only after manager acknowledgement; never release a lease."""
        with self._lock:
            if key in self._frontier:return False
        if self._continuation_owner is not None and self._continuation_owner.retained(handle):return False
        if self._timed_blocked(handle) or self._intent_retained(handle):return False
        if handle.unresolved or not handle.token.cancelled or handle.wait(0) is not True:
            return False
        manager=self._host_manager
        if manager is not None:
            self.claim_host(self._host)
            if manager.acknowledge_deadline_retirement(key,handle.token,handle) is not True:
                return False
        with self._lock:
            if self._handles.get(key) is not handle:
                return False
            del self._handles[key]
        return True

    def start(self, task, token):
        if self._host is not None:self.claim_host(self._host)
        if not isinstance(token,CancellationToken):raise RuntimeUnavailable('Exact manager token required')
        token.check()
        task,scope=self._prepare(task); key=str(int(task['id']))
        with self._lock:
            if key in self._frontier:raise TaskConflict('Frontier generation still owns retirement')
            old=self._handles.get(key)
        if old is not None and old.token is not token:
            if not self._retire_cancelled(key,old):
                raise TaskConflict('Prior engine generation still owns work')
        with self._lock:
            old=self._handles.get(key)
            if old is not None:
                if old.token is token:
                    if (isinstance(old,BootstrapHandle) and scope is not None) or (not isinstance(old,BootstrapHandle) and old.scope!=scope):raise TaskConflict('Engine scope changed within generation')
                    token.check();return old
                raise TaskConflict('Prior engine generation still owns work')
            if len(self._handles)>=self.max_pending:raise RuntimeUnavailable('Engine capacity exhausted')
            token.check()
            if scope is None:
                handle=self._bootstrap_factory(deepcopy(task),token,self._host_manager)
                if (type(handle) is not BootstrapHandle or handle.task_id!=key or handle.exploration_id!=task['exploration_id']
                    or handle.token is not token or handle.manager is not self._host_manager or handle.scope is not None
                    or handle.phase!='installed' or handle.unresolved or handle.wait(0) is not True):
                    raise RuntimeUnavailable('Factory must return an exact inert bootstrap handle')
                try:
                    if handle.validate_construction() is not True:
                        raise RuntimeUnavailable('Bootstrap construction validation required')
                except Exception:
                    raise RuntimeUnavailable('Bootstrap construction binding changed') from None
            else:
                handle=self._factory(deepcopy(task),token,scope)
                if not isinstance(handle,TaskEngineHandle) or handle.scope!=scope or handle.token is not token:
                    raise RuntimeUnavailable('Factory must return an unstarted exact engine handle')
            if self._host is not None:
                if (self._host.manager is not self._host_manager or self._host.manager.runner is not self or self._host.runner is not self
                    or self._host.manager.repository.executor is not self._host_executor
                    or (handle.executor if isinstance(handle,BootstrapHandle) else handle.store.executor) is not self._host_executor):
                    raise RuntimeUnavailable('Engine graph and hosted task database identities must match')
            # Retain ownership even if cancellation arrives during construction.
            self._handles[key]=handle
            if token.cancelled:handle.cancel(token.reason)
            return handle

    def handles(self):
        with self._lock:return tuple(self._handles.values())

    def advance(self, task_id, token, now):
        with self._lock:
            handle=self._handles.get(str(task_id))
            if handle is None or handle.token is not token:raise TaskConflict('Engine generation not owned')
        if self._intent_retained(handle):raise TaskConflict('Intent control retains generation; inspect before advance')
        binder=self._timed_binding()
        if binder is None:return handle.advance(now)
        if (self._host.owns_work!=self._host_work_check or self._host_work_check(self) is not True):
            raise TaskConflict('Timed registration requires original owned-work slot')
        if self._timed_blocked(handle):raise TaskConflict('Timed registration retained or missing; no replay')
        if isinstance(handle,BootstrapHandle) and handle.scope is not None and handle.prepared_timeout_seconds>0:
            if binder.actions(handle).finalizer is None:
                raise RuntimeUnavailable('Explicit finalization configuration is incomplete')
        result=handle.advance(now)
        if getattr(result,'action',None)=='bootstrap_prepared':
            with self._lock:
                if self._handles.get(str(task_id)) is not handle or handle in self._timed_attempts:
                    raise TaskConflict('Prepared registration cannot adopt or retry')
                self._timed_attempts[handle]='registering'
            try:
                self._timed_binding()
                if self._host_work_check(self) is not True:raise TaskConflict('Owned-work slot lost')
                actions=binder.register_prepared(handle)
                self._timed_binding()
                if handle.prepared_timeout_seconds>0:
                    if not binder.registered(handle) or binder.actions(handle) is not actions:
                        raise RuntimeUnavailable('Prepared actions not retained')
                    if actions.finalizer is not None:
                        from .owned_finalization import OwnedFinalization
                        finalizer=actions.finalizer
                        names=('bind_runner_retirement','retirement_result','confirm_runner_removal')
                        methods=tuple(getattr(finalizer,n) for n in names)
                        if type(finalizer) is not OwnedFinalization or any(getattr(m,'__func__',None) is not getattr(OwnedFinalization,n) for n,m in zip(names,methods)):
                            raise RuntimeUnavailable('Original final retirement methods required')
                        methods[0](binder,actions)
                        with self._lock:self._final_retirement[handle]=(binder,actions,finalizer,methods)
                        handle._child.bind_timed_execution(binder,actions)
                        self._timed_binding()
                with self._lock:
                    if self._handles.get(str(task_id)) is not handle:raise TaskConflict('Prepared owner changed')
                    self._timed_attempts[handle]='registered'
            except BaseException:
                with self._lock:self._timed_attempts[handle]='failed'
                raise
        return result

    def settle_completed(self, complete):
        if not callable(complete):raise ValueError('Completion callback required')
        settled=[]
        # Snapshot under the runner lock; manager and handle callbacks run outside
        # it to avoid reversing manager/engine lifetime lock ordering.
        with self._lock:
            inventory=tuple(self._handles.items());frontier=tuple(self._frontier.items())
        bound_keys={key for key,entry in frontier}
        for key,entry in frontier:
            if self._timed_blocked(entry[0]) or self._intent_retained(entry[0]):continue
            if self._settle_frontier(key,entry):settled.append(key)
        for key,handle in inventory:
            if key in bound_keys:continue
            if self._timed_blocked(handle) or self._intent_retained(handle):continue
            if isinstance(handle,BootstrapHandle) and handle.prepared_timeout_seconds>0 and not handle.token.cancelled:
                # A positive generation cannot fall back to zero-engine completion.
                continue
            if handle.unresolved:continue
            if handle.token.cancelled:
                self._retire_cancelled(key,handle)
                continue
            if handle.settle_completed(complete) is True:
                with self._lock:
                    if self._handles.get(key) is handle:
                        del self._handles[key];settled.append(key)
        return tuple(settled)
