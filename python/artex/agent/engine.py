"""Explicit serial exploration engine for an initialized single-process task.

Each advance performs at most ONE real PlannerCycle or IntentWorker operation.
No thread/poller is created; callbacks can block, so their own budgets still apply.
This bridges durable graph/transcript components, not task creation/bootstrap.
A host must tick it and fence all graph/config/lifecycle writers with the SAME
reentrant guard/current authority. No multi-process lease or restart recovery is
claimed. Unowned running intents and ambiguous effects quarantine the handle.

Conservative completion: persisted nonempty settled goals (or explicit manual
mode), no open/running/paused intents, no unsettled child. Unlike Go goal-met
cancellation, this first bridge drains selected work naturally; it does not kill
remaining open work, revive tasks, or use model text as completion proof. Main
chat, archive/delete and external graph mutations must honor completion fencing.
No deadline final-planning parity: nonzero task timeout is rejected before work.
Task activity projection/streaming is not supplied by this module.
"""
from dataclasses import dataclass,replace,asdict
from threading import Condition,RLock,get_ident
from time import monotonic
import math,uuid,json,hashlib
from .planner import PlannerCycle,PlannerScope
from .worker import IntentWorker,WorkerScope
from ..manager import CancellationToken
from ..db.exploration import ExplorationStore
from ..db.core import Query,int64
from ..sidequestion import Model

class EngineUnavailable(RuntimeError):pass
class EngineUnresolved(RuntimeError):pass

@dataclass(frozen=True)
class EngineScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str

@dataclass(frozen=True)
class EngineProgress:
    action:str
    intent_id:int=0
    completion_ready:bool=False
    cancelled:bool=False
    unresolved:bool=False

class TaskEngineHandle:
    def __init__(self,scope,token,*,store,planner,planner_scope,worker,worker_model,
                 worker_system,owner,current,lifecycle_guard,single_process,
                 manual_mode=False,heartbeat_seconds=600,max_nodes=5000,max_events=100,activity_projector=None,cleanup_authorize=None,cleanup_current=None,timing=None,process=None):
        if not isinstance(scope,EngineScope) or not isinstance(token,CancellationToken):raise ValueError('Exact task scope and manager token required')
        for value in (scope.task_id,scope.exploration_id,scope.run_id):
            if int64(value,'engine identity')<=0:raise ValueError('Positive engine identity required')
        if not isinstance(scope.revision,str) or not scope.revision:raise ValueError('Revision required')
        if not isinstance(store,ExplorationStore) or store.read_only or store.exploration_id!=scope.exploration_id:raise ValueError('Exact writable graph required')
        if not isinstance(planner,PlannerCycle) or not isinstance(worker,IntentWorker):raise ValueError('Actual planner and worker required')
        if not isinstance(planner_scope,PlannerScope) or self._identity(planner_scope)!=self._identity(scope):raise ValueError('Planner scope mismatch')
        if planner.store is not store or worker.store.executor is not store.executor or (worker.store.task_id,worker.store.exploration_id)!=(scope.task_id,scope.exploration_id):raise ValueError('Component graph scope mismatch')
        if not isinstance(worker_model,Model) or not worker_model.identity or not isinstance(worker_system,str) or not isinstance(owner,str) or not owner:raise ValueError('Explicit worker model/prompt/owner required')
        if single_process is not True or type(manual_mode) is not bool:raise ValueError('Explicit single-process ownership required')
        if not callable(current) or not callable(lifecycle_guard):raise ValueError('Shared lifecycle authority required')
        if type(heartbeat_seconds) is not int or heartbeat_seconds<600:raise ValueError('Heartbeat minimum is 600 seconds')
        if type(max_nodes) is not int or not 1<=max_nodes<=10000 or type(max_events) is not int or not 1<=max_events<=1000:raise ValueError('Invalid engine bounds')
        if activity_projector is not None:
            from .activity_projection import ActivityProjector
            if type(activity_projector) is not ActivityProjector or activity_projector.executor is not store.executor or activity_projector.scope is not scope:raise ValueError('Exact scoped activity projector required')
        if (cleanup_authorize is None)!=(cleanup_current is None) or cleanup_authorize is not None and (not callable(cleanup_authorize) or not callable(cleanup_current)):raise ValueError('Explicit paired cleanup policies required')
        if timing is not None:
            from .task_timing import PreparedTaskTiming
            if type(timing) is not PreparedTaskTiming or timing.scope is not scope:raise ValueError('Exact scoped timing binding required')
        if process is not None:
            from .task_process import PreparedWorkerProcess
            if type(process) is not PreparedWorkerProcess or process.scope is not scope or cleanup_authorize is None:
                raise ValueError('Exact scoped process binding and cleanup authority required')
        self.process=self._process=process
        self.timing=timing;self._timing=timing
        self._timed_execution=None;self._timed_bind_attempted=False
        self._cleanup_authorize=cleanup_authorize;self._cleanup_current=cleanup_current;self.cleanup=None;self._cleanup_owner=None
        self.activity_projector=activity_projector;self._activity_projector=activity_projector
        self.scope,self.token,self.store=scope,token,store
        self.planner,self.planner_scope,self.worker=planner,planner_scope,worker
        self.worker_model,self.worker_system,self.owner=worker_model,worker_system,owner
        self.current,self.guard=current,lifecycle_guard
        self.manual_mode,self.heartbeat,self.max_nodes,self.max_events=manual_mode,heartbeat_seconds,max_nodes,max_events
        self._condition=Condition(RLock());self._active=False;self._thread=None;self._child=None
        self._events=[];self._event_ids=set();self._last_plan=None;self._started=False
        self._unresolved=False;self._closed=False;self._completed=False;self._wake=None
    def bind_cleanup(self,manager,outer):
        if self._cleanup_authorize is None:return
        from .inflight_cleanup import InflightCleanupOwner
        with self._condition:
            if self._active or self._started or self.cleanup is not None:raise EngineUnavailable('Cleanup binding requires inert engine')
            if self.planner.transcript is not self.worker.transcript:raise EngineUnavailable('One canonical transcript required')
            owner=InflightCleanupOwner(manager,outer,self.token,self.scope,transcript=self.planner.transcript,
                worker_store=self.worker.store,projector=self.activity_projector,current=self.planner.current,
                configuration_current=self._cleanup_current,lifecycle_guard=self.guard,authorize=self._cleanup_authorize)
            self.planner.bind_cleanup(owner);self.worker.bind_cleanup(owner);self.cleanup=owner;self._cleanup_owner=owner

    def bind_timing(self,manager,outer):
        if self.timing is not self._timing:raise EngineUnavailable('Timing binding changed')
        if self._timing is None:return
        with self._condition:
            if self._active or self._started:raise EngineUnavailable('Timing binding requires inert engine')
            self._timing.bind(manager,outer,self)

    def bind_timed_execution(self,binder,actions):
        from .timed_lifecycle import PreparedTimedLifecycle,PreparedTimedActions
        if type(binder) is not PreparedTimedLifecycle or type(actions) is not PreparedTimedActions:
            raise EngineUnavailable('Exact lifecycle registration required')
        with self._condition:
            if self._timed_bind_attempted or self._active or self._started:
                raise EngineUnavailable('Timed execution binding cannot be retried')
            self._timed_bind_attempted=True
        try:
            outer=actions.handle
            if (self._timing is None or actions.timing is not self._timing or outer._child is not self
                or self.cleanup is None or self.cleanup is not self._cleanup_owner
                or binder.application.host.owns_work(binder.application.runner) is not True):
                raise EngineUnavailable('Original owned prepared engine required')
            check=binder.validate_execution_registration
            if check(outer,actions) is not True:raise EngineUnavailable('Timed registration unavailable')
            self._timed_execution=(binder,actions,outer,check,self._timing.execution_status)
            self._execution_registration()
            return True
        except BaseException:
            with self._condition:self._unresolved=True
            raise EngineUnresolved('Timed execution binding retained for reconciliation') from None
    def _execution_registration(self):
        value=self._timed_execution
        if value is None:return None
        binder,actions,outer,check,status=value
        if (binder.validate_execution_registration!=check or self._timing.execution_status!=status or actions.timing is not self._timing
            or outer._child is not self or self.token is not outer.token
            or check(outer,actions) is not True):raise EngineUnavailable('Timed registration changed')
        return actions
    def _execution_status(self):
        actions=self._execution_registration()
        if actions is None:return None
        status=self._timing.execution_status()
        self._execution_registration()
        return status

    def bind_process(self,manager,outer):
        if self.process is not self._process:raise EngineUnavailable('Process binding changed')
        if self._process is not None:
            with self._condition:
                if self._active or self._started:raise EngineUnavailable('Process binding requires inert engine')
                self._process.bind(manager,outer,self)

    def _process_drained(self):
        if self.process is not self._process:raise EngineUnavailable('Process binding changed')
        return self._process is None or self._process.drained()

    @staticmethod
    def _identity(scope):return (scope.task_id,scope.exploration_id,scope.run_id,scope.revision)
    def _check(self):
        if not self._process_drained():raise EngineUnresolved('Process effects require reconciliation')
        if self.timing is not self._timing:raise EngineUnavailable('Timing binding changed')
        if self._timing is not None:self._timing.validate_bindings()
        self._execution_registration()
        if self.current(self.scope,self.token) is not True:raise EngineUnavailable('Engine generation changed')
        if self.token.cancelled:raise EngineUnavailable('Engine cancelled')
    def _snapshot(self):
        self._check()
        # Readiness is stronger than a bare model-ready boolean. No inference of
        # task ownership from the supplied exploration/session identifier.
        actions=self._execution_registration()
        if actions is None:
            rows=self.store.executor.fetch_all(Query('''SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND timeout_seconds=0 AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1)''',
                (self.scope.task_id,self.scope.exploration_id)))
            if rows not in ([(self.scope.task_id,)],[[self.scope.task_id]]):raise EngineUnavailable('Task lifecycle, deadline or ownership unsupported')
        else:
            r=actions.receipt;timeout=actions.handle.prepared_timeout_seconds
            rows=self.store.executor.fetch_all(Query('''SELECT t.id,t.first_run_at,t.deadline_at FROM tasks t
JOIN public.artex_task_bootstrap b ON b.task_id=t.id AND b.exploration_id=t.exploration_id
WHERE t.id=$1 AND t.exploration_id=$2 AND t.status='running' AND NOT t.paused AND NOT t.queued
AND t.deleted_at IS NULL AND t.archived_at IS NULL AND t.timeout_seconds=$3
AND b.run_id=$4 AND b.request_id=$5 AND b.config_digest=$6 AND b.owner_token=$7 AND b.revision=$8 AND b.phase='prepared'
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=t.id)''',
                (r.task_id,r.exploration_id,timeout,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
            lease=actions.timing.store.lease
            expected=(r.task_id,None,None) if lease is None else (r.task_id,lease.clock.first_run_at,lease.clock.deadline_at)
            if len(rows)!=1 or tuple(rows[0])!=expected:raise EngineUnavailable('Exact timed task snapshot unavailable')
            self._check()
        nodes=self.store.nodes(self.max_nodes+1)
        if len(nodes)>self.max_nodes:raise EngineUnavailable('Graph exceeds engine bound')
        goals=[n for n in nodes if n.kind=='goal']
        if not goals and not self.manual_mode and actions is None:raise EngineUnavailable('Goal bootstrap required before engine admission')
        if any(n.kind=='intent' and n.state=='running' for n in nodes):raise EngineUnresolved('Unowned running intent requires reconciliation')
        complete=(bool(goals) or self.manual_mode) and all(n.state in ('met','abandoned') for n in goals) and not any(n.kind=='intent' and n.state in ('open','running','paused') for n in nodes)
        return nodes,complete if actions is None else False
    def notify(self,event_id,kind,intent_id=0,detail=''):
        if not isinstance(event_id,str) or not event_id or len(event_id)>128 or kind not in ('done','finding','hint','manual'):raise ValueError('Invalid bounded engine event')
        if int64(intent_id,'intent ID')<0 or not isinstance(detail,str) or len(detail.encode('utf-8'))>8192:raise ValueError('Invalid event data')
        with self._condition:
            if self._closed or self._unresolved:return False
            if event_id in self._event_ids:return False
            # Never evict identity receipts and permit duplicate wakes later.
            if len(self._event_ids)>=self.max_events:raise EngineUnavailable('Event identity capacity exhausted')
            self._event_ids.add(event_id);self._events.append(dict(kind=kind,intent_id=intent_id,detail=detail));return True
    def cancel(self,reason='paused_by_user'):
        with self._condition:
            self._closed=True;self.token.cancel(reason);child=self._child
        if child:
            if child[0]=='planner':self.planner.cancel(self.scope.task_id)
            else:self.worker.cancel(child[1],'task_pause')
    def wait(self,timeout=None):
        if timeout is not None and (isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<0):raise ValueError('Finite nonnegative timeout required')
        with self._condition:
            if self._active and self._thread==get_ident():raise EngineUnavailable('Engine cannot wait for itself')
            return self._condition.wait_for(lambda:not self._active,timeout) and self._process_drained()
    @property
    def task_id(self):return str(self.scope.task_id)
    @property
    def unresolved(self):
        with self._condition:return self._unresolved or (not self._active and not self._process_drained())
    def advance(self,now):
        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now) or now<0:raise ValueError('Finite monotonic tick required')
        with self._condition:
            if self._unresolved:raise EngineUnresolved('Engine requires reconciliation; do not retry work')
            if self._active:return EngineProgress('busy')
            if self._closed or self.token.cancelled:return EngineProgress('closed',cancelled=self.token.cancelled)
            self._active=True;self._thread=get_ident()
        effects=False;cleanup_finished=False
        try:
            with self.guard(self.scope):
                self._check()
                timing_status=self._execution_status()
                if timing_status in ('expired','busy'):return EngineProgress('expiry_wait' if timing_status=='expired' else 'busy')
                nodes,complete=self._snapshot()
                if complete:return EngineProgress('idle',completion_ready=True)
                timing_status=self._execution_status()
                if timing_status in ('expired','busy'):return EngineProgress('expiry_wait' if timing_status=='expired' else 'busy')
                active=any(n.kind=='intent' and n.state in ('open','running') for n in nodes)
                open_goals=any(n.kind=='goal' and n.state=='open' for n in nodes)
                with self._condition:
                    due=bool(self._events) or (not self._started and not active) or (self._last_plan is not None and now-self._last_plan>=self.heartbeat)
                    if self._last_plan is None and self._started and self._start_time+self.heartbeat<=now:due=True
                    if not self._started:self._start_time=now
                    self._started=True
                    triggers=list(self._events)
                if open_goals and due:
                    wake=self.planner.prepare(self.planner_scope,uuid.uuid4().hex,triggers)
                    self._wake=wake
                    with self._condition:
                        del self._events[:len(triggers)]
                        self._child=('planner',0)
                    self._check()
                    action='planner';record=None
                else:
                    frontier=self.store.frontier(20)
                    record=None
                    for node in frontier:
                        candidate=self.worker.store.get(node.id)
                        if candidate is not None and candidate.node.state=='open':record=candidate;break
                    if record is None:return EngineProgress('idle')
                    worker_scope=WorkerScope(self.scope.task_id,self.scope.exploration_id,self.scope.run_id,self.scope.revision,
                        record.id,record.version,self.owner,self.worker_model,self.worker_system)
                    with self._condition:self._child=('worker',record.id)
                    self._check();action='worker'
            # Do not hold the task guard over a model call: callbacks acquire it
            # for individual effects. Host current callbacks must include token.
            effects=True
            if action=='planner':
                outcome=self.planner.run(wake)
                if self.cleanup is not self._cleanup_owner:raise EngineUnresolved('Cleanup owner changed')
                if self.activity_projector is not self._activity_projector:raise EngineUnresolved('Activity projector changed')
                if self.activity_projector is not None:
                    identity=dict(wake.scope.identity(),limits_digest=hashlib.sha256(json.dumps(asdict(self.planner.limits),sort_keys=True,allow_nan=False).encode()).hexdigest(),tool_catalog_digest=self.planner._tool_catalog_digest)
                    if outcome.cleanup is not None:self.cleanup.project(outcome.cleanup,self.activity_projector,outcome.outcome,identity)
                    else:self.activity_projector.project('planner',wake.scope,outcome.outcome,identity)
                if outcome.cleanup is not None:
                    self.cleanup.finish(outcome.cleanup);cleanup_finished=True
                with self._condition:self._last_plan=now;self._wake=None
            else:
                outcome=self.worker.run(worker_scope,uuid.uuid4().hex)
                if self.cleanup is not self._cleanup_owner:raise EngineUnresolved('Cleanup owner changed')
                if self.activity_projector is not self._activity_projector:raise EngineUnresolved('Activity projector changed')
                if self.activity_projector is not None:
                    if outcome.cleanup is not None:self.cleanup.project(outcome.cleanup,self.activity_projector,outcome.outcome,worker_scope.identity(self.worker.limits))
                    else:self.activity_projector.project('worker',worker_scope,outcome.outcome,worker_scope.identity(self.worker.limits))
                if outcome.cleanup is not None:
                    self.cleanup.finish(outcome.cleanup);cleanup_finished=True
                with self._condition:
                    if len(self._event_ids)>=self.max_events:raise EngineUnresolved('Completed worker event requires reconciliation')
                    event_id=f'worker:{record.id}:{outcome.version}'
                    self._event_ids.add(event_id);self._events.append(dict(kind='done',intent_id=record.id,detail=outcome.state))
            terminal=outcome.outcome
            if terminal.result is not None and terminal.result.reason=='task_deadline_expired' and self._timed_execution is not None:
                if not cleanup_finished or not terminal.persisted or terminal.stale or terminal.reused:
                    raise EngineUnresolved('Task expiry cleanup unconfirmed')
                return EngineProgress('expiry_wait',record.id if record else 0)
            # PlannerResult is observation, never an atomic completion decision.
            return EngineProgress(action,record.id if record else 0,cancelled=self.token.cancelled)
        except BaseException as error:
            if effects or isinstance(error,EngineUnresolved):
                with self._condition:self._unresolved=True
                raise EngineUnresolved('Child effects or settlement unconfirmed; reconcile before retry') from None
            raise
        finally:
            with self._condition:self._active=False;self._thread=None;self._child=None;self._condition.notify_all()
    def settle_completed(self,complete):
        """After enclosing manager admission returns, recheck and exact-token CAS.

        Callback MUST be manager.complete and all competing graph writers must
        honor the same guard. Never use cached completion_ready as authority.
        Completion does not cancel the manager token. False keeps handle owned.
        """
        if not callable(complete):raise ValueError('Exact-token completion callback required')
        if self._timed_execution is not None:return False
        with self._condition:
            if self._completed:return True
            if self._active or self._unresolved or self.token.cancelled or not self._process_drained():return False
            self._active=True;self._thread=get_ident()
        attempted=False
        try:
            with self.guard(self.scope):
                _,ready=self._snapshot()
                if not ready:return False
                attempted=True
                accepted=complete(str(self.scope.task_id),self.token,'done')
                if accepted is not True:return False
                with self._condition:self._closed=True;self._completed=True
                return True
        except BaseException:
            if attempted:
                with self._condition:self._unresolved=True
                raise EngineUnresolved('Task completion persistence uncertain') from None
            raise
        finally:
            with self._condition:self._active=False;self._thread=None;self._condition.notify_all()
