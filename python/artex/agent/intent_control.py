"""Opt-in exact local worker pause/resume. No routes, task resurrection or model.

Pause requires an already CREATED canonical worker turn owned by the actual
engine. A bounded wait may return pending, never pretend the writer stopped.
Resume changes only one paused/version row and leaves execution to the pump.
Unknown effects retain the exact reservation and quarantine; never replay.
"""
from dataclasses import dataclass,asdict
from threading import RLock
import math,json
from ..db.core import Query

class IntentControlUnavailable(RuntimeError):pass
class IntentControlUncertain(IntentControlUnavailable):pass
_KEY=object()
def _same_value(a,b):
    return type(a) is type(b) and json.dumps(asdict(a),sort_keys=True,allow_nan=False)==json.dumps(asdict(b),sort_keys=True,allow_nan=False)
@dataclass(frozen=True,eq=False,init=False)
class IntentControlReservation:
    adapter:object
    engine:object
    intent_id:int
    action:str
    scope:object
    def __init__(self,key,adapter,engine,intent_id,action,scope):
        if key is not _KEY:raise IntentControlUnavailable('Private engine reservation required')
        for k,v in locals().copy().items():
            if k not in ('self','key'):object.__setattr__(self,k,v)
@dataclass(frozen=True)
class IntentControlProposal:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    intent_id:int
    action:str
    expected_version:int
@dataclass(frozen=True)
class IntentControlResult:
    phase:str
    intent_id:int
    state:str
    version:int

class IntentControl:
    def __init__(self,host,*,authorize):
        from .host import AgentHost
        if type(host) is not AgentHost or not callable(authorize):raise ValueError('Exact host and independent intent-control grant required')
        self.host=host;self.authorize=authorize
        self._pins=(host,host.manager,host.runner,host.manager.repository,host.manager.repository.executor,authorize)
        self._original_pins=self._pins
        self._slot_method=AgentHost.owns_intent_control
        self._lock=RLock();self._records={}
    def validate_bindings(self):
        h,m,r,repo,e,a=self._pins
        if self._pins is not self._original_pins or self.host is not h or self.authorize is not a or h.manager is not m or h.runner is not r or m.runner is not r or m.repository is not repo or repo.executor is not e:raise IntentControlUnavailable('Intent-control binding changed')
        return True
    def retained(self,outer):
        self.validate_bindings()
        with self._lock:return outer in self._records
    def _owns_engine(self,engine,intent_id,action):
        from .host import AgentHost
        self.validate_bindings();h,m,r,repo,e,a=self._pins
        if AgentHost.owns_intent_control is not self._slot_method or self._slot_method(h,r,str(engine.scope.task_id),intent_id,action) is not True:raise IntentControlUnavailable('Original host intent-control slot required')
    def _coordinator(self,record):
        outer=record['pins'][0]
        c,method,current,guard,factory,journal,executor=record['coordinator']
        from .bootstrap import BootstrapCoordinator
        if (outer.coordinator is not c or c._check!=method
            or getattr(method,'__func__',None) is not BootstrapCoordinator._check
            or any(a is not b for a,b in zip((c.current,c.guard,c.factory,c.journal,c._journal,c._executor,c.journal.executor),
                                              (current,guard,factory,journal,journal,executor,executor)))):
            raise IntentControlUnavailable('Original coordinator validation changed')
        return method
    def bind_frozen_inspection(self,outer,intent_id,observer):
        """Retain only the original registered freeze for an existing pending pause."""
        self.validate_bindings()
        with self._lock:rec=self._records.get(outer)
        if rec is None or rec['failed'] or rec['pins'][3].action!='pause' or rec['pins'][3].intent_id!=intent_id:raise IntentControlUnavailable('Exact pending pause required')
        self._owns_engine(rec['pins'][1],intent_id,'inspect')
        from .expiry_observer import OwnedExpiryObserver
        from .engine_runner import EngineRunner
        from .timed_lifecycle import PreparedTimedLifecycle
        runner=self._pins[2]
        if type(observer) is not OwnedExpiryObserver or getattr(runner._timed_binding,'__func__',None) is not EngineRunner._timed_binding:raise IntentControlUnavailable('Original expiry registration required')
        binder=EngineRunner._timed_binding(runner)
        if type(binder) is not PreparedTimedLifecycle or getattr(binder.actions,'__func__',None) is not PreparedTimedLifecycle.actions:raise IntentControlUnavailable('Registered timing required')
        actions=PreparedTimedLifecycle.actions(binder,outer)
        if actions.handle is not outer or actions.observer is not observer or actions.timing.kernel is not observer.clock or observer.host is not self.host:raise IntentControlUnavailable('Original observation owner required')
        frozen=(observer,observer._lease,observer._freeze,binder,actions,observer.clock,observer.clock.store)
        if rec.get('frozen') is not None and any(a is not b for a,b in zip(rec['frozen'],frozen)):raise IntentControlUnavailable('Frozen inspection changed')
        rec['frozen']=frozen
        self._generation(rec,'inspect')
    def _generation(self,rec,operation):
        outer,engine,cap,p,guard,worker,transcript=rec['pins'];manager=self._pins[1]
        frozen=rec.get('frozen')
        if frozen is None:
            if not manager.owns_generation(str(p.task_id),outer.token,outer):raise IntentControlUnavailable('Task no longer runnable')
            return
        if operation!='inspect' or p.action!='pause':raise IntentControlUnavailable('Frozen inspection only')
        from .expiry_observer import OwnedExpiryObserver
        from ..manager import TaskManager
        from .timed_lifecycle import PreparedTimedLifecycle
        observer,lease,freeze,binder,actions,kernel,store=frozen
        if (lease is None or freeze is None or observer._transfer is not None or observer._failed
            or observer._lease is not lease or observer._freeze is not freeze or observer.clock is not kernel
            or kernel.store is not store or store.handle is not outer or store.manager is not manager
            or store.lease is not lease or observer.host is not self.host
            or getattr(binder.actions,'__func__',None) is not PreparedTimedLifecycle.actions
            or PreparedTimedLifecycle.actions(binder,outer) is not actions or actions.observer is not observer
            or getattr(manager.validate_deadline_observation,'__func__',None) is not TaskManager.validate_deadline_observation):
            raise IntentControlUnavailable('Original frozen pause ownership changed')
        OwnedExpiryObserver._check(observer)
        TaskManager.validate_deadline_observation(manager,str(p.task_id),outer.token,outer,lease,freeze)
    def _check(self,record,operation=None,*,callbacks=True):
        self.validate_bindings();h,m,r,repo,e,a=self._pins
        outer,engine,cap,p,guard,worker,transcript=record['pins']
        coordinator_check=self._coordinator(record)
        if (type(p) is not IntentControlProposal or type(cap) is not IntentControlReservation
            or type(cap.intent_id) is not int or cap.scope is not record['scope']
            or json.dumps(asdict(p),sort_keys=True,allow_nan=False)!=record['proposal']
            or (cap.scope is not None and json.dumps(asdict(cap.scope),sort_keys=True,allow_nan=False)!=record['scope_values'])
            or (worker.cancel,engine.wait,transcript.finalized_scoped_result)!=record['methods']):
            raise IntentControlUnavailable('Original intent control evidence changed')
        action=operation or p.action
        self._owns_engine(engine,p.intent_id,action)
        from .engine import TaskEngineHandle
        if type(engine) is not TaskEngineHandle or outer._child is not engine or outer.token is not engine.token or engine.worker is not worker or worker.transcript is not transcript or engine.store.executor is not e or outer.coordinator.guard is not guard or engine._intent_control is not cap:
            raise IntentControlUnavailable('Original intent generation changed')
        if (cap.adapter is not self or cap.engine is not engine or cap.intent_id!=p.intent_id or cap.action!=p.action
            or (engine.scope.task_id,engine.scope.exploration_id,engine.scope.run_id,engine.scope.revision)!=(p.task_id,p.exploration_id,p.run_id,p.revision)):
            raise IntentControlUnavailable('Intent-control scope changed')
        self._generation(record,action)
        outer._binding()
        if callbacks:coordinator_check(p.task_id,p.exploration_id,outer.policy_revision)
        self._coordinator(record)
        engine.cleanup._binding()
        self._generation(record,action)
        if outer._child is not engine or engine.worker is not worker or engine._intent_control is not cap:raise IntentControlUnavailable('Intent ownership changed during validation')
        if engine.unresolved:raise IntentControlUncertain('Worker state unresolved')
    def _allow(self,rec,operation=None):
        self._check(rec,operation)
        if self._pins[5](rec['pins'][3]) is not True:raise IntentControlUnavailable('Intent-control denied')
        self._check(rec,operation,callbacks=False)
    def execute(self,outer,intent_id,action,timeout):
        if type(intent_id) is not int or not 0<intent_id<2**63 or action not in ('pause','resume') or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<=timeout<=30:raise ValueError('Bounded explicit intent control required')
        self.validate_bindings();engine=outer._child
        if engine is None or engine.cleanup is None:raise IntentControlUnavailable('Prepared cleanup-enabled engine required')
        with self._lock:
            if outer in self._records:raise IntentControlUnavailable('Prior intent control retained')
        cap=engine.reserve_intent_control(self,intent_id,action)
        scope=engine.scope;version=cap.scope.expected_version if cap.scope is not None else -1
        p=IntentControlProposal(scope.task_id,scope.exploration_id,scope.run_id,scope.revision,intent_id,action,version)
        rec={'pins':(outer,engine,cap,p,outer.coordinator.guard,engine.worker,engine.worker.transcript),'attempted':False,'failed':False,'cleanup':None,
             'scope':cap.scope,'scope_values':None if cap.scope is None else json.dumps(asdict(cap.scope),sort_keys=True,allow_nan=False),
             'proposal':json.dumps(asdict(p),sort_keys=True,allow_nan=False),
             'methods':(engine.worker.cancel,engine.wait,engine.worker.transcript.finalized_scoped_result),
             'coordinator':(outer.coordinator,outer.coordinator._check,outer.coordinator.current,outer.coordinator.guard,outer.coordinator.factory,outer.coordinator.journal,outer.coordinator._executor)}
        with self._lock:self._records[outer]=rec
        try:
            if action=='pause':
                owner=engine.cleanup
                with owner._lock:
                    matches=[(c,v) for c,v in owner._records.items() if v['role']=='worker' and _same_value(v['scope'],cap.scope) and v['stage']=='pending' and not v['failed']]
                if len(matches)!=1:raise IntentControlUnavailable('Already admitted canonical worker turn required')
                rec['cleanup']=matches[0][0];rec['turn']=matches[0][1]['turn']
                self._allow(rec)
                rec['attempted']=True
                if engine.worker.cancel(intent_id,'worker_pause') is not True:raise IntentControlUncertain('Worker cancellation not acknowledged')
                return self._finish_pause(rec,timeout)
            return self._resume(rec)
        except BaseException:
            if rec['attempted']:
                rec['failed']=True;engine.finish_intent_control(cap,uncertain=True)
                raise IntentControlUncertain('Intent operation unconfirmed; ownership retained') from None
            engine.finish_intent_control(cap)
            with self._lock:self._records.pop(outer,None)
            raise
    def _parent(self,tx,p):
        rows=tx.fetch_all(Query("""SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE""",(p.task_id,p.exploration_id)))
        if rows!=[(p.task_id,)]:raise IntentControlUnavailable('Local active task required')
    def _finish_pause(self,rec,timeout,operation=None):
        outer,engine,cap,p,guard,worker,transcript=rec['pins']
        self._allow(rec,operation)
        stopped=engine.wait(timeout)
        self._allow(rec,operation)
        if stopped is not True:return IntentControlResult('pending',p.intent_id,'running',p.expected_version+1)
        saved=engine._control_outcome
        if saved is None or saved[1] is not True:raise IntentControlUncertain('Canonical cleanup not confirmed')
        outcome=saved[0];turn=outcome.outcome
        if type(outcome.intent_id) is not int or type(outcome.version) is not int or outcome.intent_id!=p.intent_id or outcome.state!='paused' or outcome.version!=p.expected_version+2 or outcome.cleanup is not rec['cleanup'] or not turn.persisted or turn.stale or turn.reused or turn.result.reason!='cancelled' or not _same_value(turn.turn,rec['turn']):
            raise IntentControlUncertain('Exact paused outcome unavailable')
        evidence=transcript.finalized_scoped_result(cap.scope.session_id,turn.turn.id,cap.scope.identity(worker.limits))
        if evidence is None or not _same_value(evidence.result,turn.result):raise IntentControlUncertain('Canonical paused receipt changed')
        with guard(p.task_id,p.exploration_id):
            self._allow(rec,operation)
            with self._pins[4].transaction() as tx:
                self._parent(tx,p)
                rows=tx.fetch_all(Query("SELECT state,content_version,owner FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 AND kind='intent' FOR UPDATE",(p.intent_id,p.exploration_id)))
                if len(rows)!=1 or len(rows[0])!=3 or type(rows[0][1]) is not int or rows!=[('paused',outcome.version,cap.scope.owner)]:raise IntentControlUncertain('Paused claim changed')
                self._allow(rec,operation)
            self._allow(rec,operation)
        self._allow(rec,operation)
        engine.finish_intent_control(cap)
        with self._lock:self._records.pop(outer,None)
        return IntentControlResult('confirmed',p.intent_id,'paused',outcome.version)
    def inspect(self,outer,intent_id,timeout):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<=timeout<=30:raise ValueError('Bounded wait required')
        self.validate_bindings()
        with self._lock:rec=self._records.get(outer)
        if rec is None or rec['failed'] or rec['pins'][3].intent_id!=intent_id or rec['pins'][3].action!='pause':raise IntentControlUnavailable('Exact pending pause required')
        try:return self._finish_pause(rec,timeout,'inspect')
        except BaseException:
            rec['failed']=True;rec['pins'][1].finish_intent_control(rec['pins'][2],uncertain=True)
            raise IntentControlUncertain('Paused writer unconfirmed; retained') from None
    def _resume(self,rec):
        outer,engine,cap,p,guard,worker,transcript=rec['pins']
        self._check(rec)
        with guard(p.task_id,p.exploration_id):
            with self._pins[4].transaction() as tx:
                self._parent(tx,p)
                rows=tx.fetch_all(Query("SELECT state,content_version FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 AND kind='intent' FOR UPDATE",(p.intent_id,p.exploration_id)))
                if len(rows)!=1 or rows[0][0]!='paused' or type(rows[0][1]) is not int or not 0<=rows[0][1]<2**31-2:raise IntentControlUnavailable('Local paused intent required')
                p=IntentControlProposal(p.task_id,p.exploration_id,p.run_id,p.revision,p.intent_id,p.action,rows[0][1])
                rec['pins']=(outer,engine,cap,p,guard,worker,transcript)
                rec['proposal']=json.dumps(asdict(p),sort_keys=True,allow_nan=False)
                self._allow(rec);rec['attempted']=True
                changed=tx.execute(Query("UPDATE exploration_nodes SET state='open',blocked_reason=NULL,completed_at=NULL,content_version=content_version+1 WHERE id=$1 AND exploration_id=$2 AND kind='intent' AND state='paused' AND content_version=$3",(p.intent_id,p.exploration_id,p.expected_version)))
                if type(changed) is not int or changed!=1:raise IntentControlUncertain('Resume CAS unavailable')
                self._allow(rec)
            self._allow(rec)
        self._allow(rec)
        engine.finish_intent_control(cap)
        with self._lock:self._records.pop(outer,None)
        return IntentControlResult('confirmed',p.intent_id,'open',p.expected_version+1)
