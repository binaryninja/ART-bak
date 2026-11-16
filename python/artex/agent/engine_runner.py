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
        self._lock=RLock(); self._handles={}; self._host=None; self._host_executor=None; self._host_manager=None
        self._frontier={};self._host_settlement_check=None

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
            self._host_executor=executor
            self._host_manager=host.manager

    def validate_create(self, description, goal, options, seed_first_intent=False):
        if self._initialization_readiness is None:
            raise RuntimeUnavailable('Engine runner requires an initialized persisted graph; creation/bootstrap unavailable')
        self._prepare({'provisional':True,'description':description,'goal':goal,
                       'options':deepcopy(options),'seed_first_intent':seed_first_intent})

    def _prepare(self, task):
        if not isinstance(task,Mapping):raise RuntimeUnavailable('Task snapshot required')
        task=deepcopy(dict(task))
        bootstrap=False
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
                if source.get('timeout_seconds',0)!=0 or source.get('deadline_at') or source.get('source_task_ids') or source.get('parent_ref') or source.get('intercept_rules'):
                    raise RuntimeUnavailable('Deadline, inherited context and intercept bootstrap unavailable')
            bootstrap=self._initialization_readiness(deepcopy(task))
            self.claim_host(self._host)
            if type(bootstrap) is not bool:raise RuntimeUnavailable('Initialization readiness must return literal bool')
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
        if task_id<=0 or exploration_id<=0 or task.get('timeout_seconds',0)!=0:
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
            if installed is not handle:raise TaskConflict('Frontier handle missing before attempt')
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
        return True

    def _retire_cancelled(self, key, handle):
        """Detach execution only after manager acknowledgement; never release a lease."""
        with self._lock:
            if key in self._frontier:return False
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
        return handle.advance(now)

    def settle_completed(self, complete):
        if not callable(complete):raise ValueError('Completion callback required')
        settled=[]
        # Snapshot under the runner lock; manager and handle callbacks run outside
        # it to avoid reversing manager/engine lifetime lock ordering.
        with self._lock:
            inventory=tuple(self._handles.items());frontier=tuple(self._frontier.items())
        bound_keys={key for key,entry in frontier}
        for key,entry in frontier:
            if self._settle_frontier(key,entry):settled.append(key)
        for key,handle in inventory:
            if key in bound_keys:continue
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
