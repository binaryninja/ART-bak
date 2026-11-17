"""Private same-process zero-timeout clean suspension and fresh engine ownership.

Never resets a token, reruns goals, adopts a SQL-only receipt, or resumes a timed
or terminal task. Ambiguous effects retain both generation references.
"""
from dataclasses import dataclass,asdict
from copy import deepcopy
from threading import RLock,Condition,get_ident
import math
import json
from ..db.task_continuation import ContinuationPersistence

class ContinuationUnavailable(RuntimeError):pass
class ContinuationUncertain(ContinuationUnavailable):pass
_KEY=object()
@dataclass(frozen=True,eq=False,init=False)
class Suspension:
    owner:object
    task_id:str
    def __init__(self,key,owner,task_id):
        if key is not _KEY:raise ContinuationUnavailable('Private suspension required')
        object.__setattr__(self,'owner',owner);object.__setattr__(self,'task_id',task_id)
@dataclass(frozen=True)
class ContinuationProposal:
    task_id:int
    exploration_id:int
    run_id:int
    configuration:str
    action:str

def _json(value):return json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':'))


def _notification_state(engine, *, stopped):
    """Bounded immutable local state, not a serialized recovery capability.

    Called only for the exact original stopped engine or inert replacement.
    Pending notification entries must still be the same objects in its queue.
    Already-consumed IDs survive for deduplication, but consumed payloads do not.
    """
    from .engine import TaskEngineHandle
    if type(engine) is not TaskEngineHandle:
        raise ContinuationUnavailable('Original continuation engine required')
    with engine._condition:
        if (engine._active or engine._unresolved or engine._wake is not None
                or engine._child is not None or engine._intent_control is not None
                or engine._closed is not stopped or engine.token.cancelled is not stopped
                or (not stopped and engine._started)):
            raise ContinuationUnavailable('Quiescent continuation notification state required')
        ids, pending, events = engine._event_ids, engine._pending_notifications, engine._events
        if (type(ids) is not set or type(pending) is not dict or type(events) is not list
                or type(engine.max_events) is not int or not 1 <= engine.max_events <= 1000
                or len(ids) > engine.max_events or len(events) > engine.max_events or len(pending) > len(ids)
                or any(type(key) is not str or not key or len(key) > 128 for key in ids)):
            raise ContinuationUnavailable('Bounded continuation notifications required')
        rows=[];seen=set();counts={}
        for event in events:counts[id(event)]=counts.get(id(event),0)+1
        for key,event in pending.items():
            if (key not in ids or type(event) is not dict or set(event) != {'kind','intent_id','detail'}
                    or type(event['kind']) is not str or event['kind'] not in ('done','finding','hint','manual')
                    or type(event['intent_id']) is not int or not 0 <= event['intent_id'] < 2**63
                    or type(event['detail']) is not str or len(event['detail'].encode('utf-8')) > 8192
                    or id(event) in seen or counts.get(id(event)) != 1):
                raise ContinuationUnavailable('Continuation notification identity changed')
            seen.add(id(event));rows.append((key,event['kind'],event['intent_id'],event['detail']))
        if [id(event) for event in events if id(event) in seen] != [id(event) for event in pending.values()]:
            raise ContinuationUnavailable('Continuation notification order changed')
        if not stopped and len(events) != len(pending):
            raise ContinuationUnavailable('Replacement contains unrelated wakes')
        return tuple(sorted(ids)),tuple(rows)


class TaskContinuationOwner:
    def __init__(self,application,*,authorize):
        from ..server.task_runtime_setup import TaskRuntimeApplication
        from .task_runtime import TaskRuntimeAssembly
        if type(application) is not TaskRuntimeApplication or type(application.runtime) is not TaskRuntimeAssembly or not callable(authorize):raise ValueError('Actual runtime application and explicit continuation grant required')
        self.application=application;self.host=application.host;self.manager=application.manager;self.runner=application.runner;self.runtime=application.runtime;self.authorize=authorize
        self._pins=(application,self.host,self.manager,self.runner,self.runtime,self.runtime.executor,authorize,self.runtime.coordinator,self.runtime.coordinator.guard)
        self._methods=(self.runtime.validate_bindings,self.runtime._bootstrap_current,self.runtime.engine_factory)
        self._db=ContinuationPersistence(self.runtime.executor);self._lock=RLock();self._records={}
        self._resume_condition=Condition(self._lock);self._resume_sealed=False;self._resume_attempts={}
    def seal_resume(self):
        """Irreversible admission fence; never releases suspension ownership."""
        self.validate_bindings()
        with self._resume_condition:
            self._resume_sealed=True;self._resume_condition.notify_all()
        return True
    def wait_resumes(self,timeout):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0:raise ValueError('Finite resume drain timeout required')
        self.validate_bindings()
        with self._resume_condition:
            if not self._resume_sealed:raise ContinuationUnavailable('Seal resume admission before drain')
            if any(thread==get_ident() for cap,thread in self._resume_attempts.values()):return False
            return self._resume_condition.wait_for(lambda:not self._resume_attempts,timeout)
    def _begin_resume(self,cap):
        """Callback-free; called under manager lock before lifecycle mutation."""
        with self._resume_condition:
            rec=self._records.get(cap)
            if self._resume_sealed:raise ContinuationUnavailable('Resume admission sealed')
            if type(cap) is not Suspension or cap.owner is not self or rec is None or rec['state']!='paused' or rec['failed'] or any(c is cap for c,t in self._resume_attempts.values()):raise ContinuationUnavailable('Exact paused resume claim required')
            attempt=object();self._resume_attempts[attempt]=(cap,get_ident());return attempt
    def _end_resume(self,cap,attempt):
        """Release only this issued outer invocation, never its task owners."""
        with self._resume_condition:
            if self._resume_attempts.get(attempt)!=(cap,get_ident()):raise ContinuationUnavailable('Original resume invocation required')
            del self._resume_attempts[attempt];self._resume_condition.notify_all()
    def _resume_open(self):
        with self._lock:
            if self._resume_sealed:raise ContinuationUnavailable('Resume admission sealed')

    def validate_bindings(self):
        a,h,m,r,t,e,grant,c,g=self._pins
        if any(x is not y for x,y in zip((self.application,self.host,self.manager,self.runner,self.runtime,self.authorize,a.host,a.manager,a.runner,a.runtime,t.executor,t.coordinator,c.guard,m.repository.executor),(a,h,m,r,t,grant,h,m,r,t,e,c,g,e))):raise ContinuationUnavailable('Continuation resources changed')
        from .task_runtime import TaskRuntimeAssembly
        if (t.validate_bindings,t._bootstrap_current,t.engine_factory)!=self._methods or any(getattr(method,'__func__',None) is not getattr(TaskRuntimeAssembly,name) for method,name in zip(self._methods,('validate_bindings','_bootstrap_current','engine_factory'))):raise ContinuationUnavailable('Runtime methods changed')
        t.validate_bindings();return True
    def _record(self,cap):
        self.validate_bindings()
        with self._lock:rec=self._records.get(cap)
        if type(cap) is not Suspension or cap.owner is not self or rec is None or cap.task_id!=rec['old'].task_id or rec['failed']:raise ContinuationUnavailable('Original suspension unavailable')
        old=rec['old']
        if old._child is not rec['engine']:raise ContinuationUnavailable('Original suspended engine changed')
        if rec['notification_state'] is not None and _notification_state(rec['engine'],stopped=True)!=rec['notification_state']:
            raise ContinuationUnavailable('Paused continuation notifications changed')
        from .bootstrap_handle import BootstrapHandle
        if any(getattr(getattr(old,name),'__func__',None) is not getattr(BootstrapHandle,name) for name in ('_binding','wait','validate_prepared_receipt')):raise ContinuationUnavailable('Original suspended handle methods required')
        old._binding()
        if old._receipt is not rec['receipt'] or _json(asdict(old._receipt))!=rec['receipt_values'] or old._configuration!=rec['configuration'] or _json([asdict(old.model),*old._configuration[1:]])!=rec['configuration_values'] or old._factory!=self._methods[2] or old.coordinator is not self._pins[7]:raise ContinuationUnavailable('Prepared suspension changed')
        return rec
    def _allow(self,cap,action):
        if action=="resume":self._resume_open()
        rec=self._record(cap);old=rec['old'];receipt=rec['receipt']
        if self._methods[1](receipt.task_id,receipt.exploration_id,old.policy_revision) is not True:raise ContinuationUnavailable('Original configuration revoked')
        if self._pins[6](ContinuationProposal(receipt.task_id,receipt.exploration_id,receipt.run_id,old.policy_revision,action)) is not True:raise ContinuationUnavailable('Continuation denied')
        self._record(cap)
        if action=="resume":self._resume_open()
        return rec
    def _registry(self,cap,phase,*,new_handle=None,new_token=None):
        from ..manager import TaskManager
        rec=self._record(cap)
        method=getattr(self.manager,'validate_suspension',None)
        if getattr(method,'__func__',None) is not TaskManager.validate_suspension or TaskManager.validate_suspension(self.manager,cap,rec['old'],rec['old'].token,phase,new_handle=new_handle,new_token=new_token) is not True:raise ContinuationUnavailable('Original manager suspension reservation required')
        return rec
    def retained(self,handle):
        self.validate_bindings()
        with self._lock:return any((r['old'] is handle or r.get('new') is handle) and r['state']!='consumed' for r in self._records.values())
    def reserve_pause(self,handle):
        from .bootstrap_handle import BootstrapHandle
        self.validate_bindings()
        if type(handle) is not BootstrapHandle or handle.manager is not self.manager or handle.coordinator is not self._pins[7] or handle.phase!='engine' or handle.prepared_timeout_seconds!=0 or type(handle.prepared_timeout_seconds) is not int or handle.unresolved or handle.token.cancelled or handle._factory!=self._methods[2]:raise ContinuationUnavailable('Original zero-timeout prepared owner required')
        if not self.manager.owns_generation(handle.task_id,handle.token,handle):raise ContinuationUnavailable('Original active generation required')
        with self._lock:
            # Consumed generations no longer provide suspension authority. Keep
            # failed or pending attempts, but do not accumulate clean history.
            for previous in tuple(self._records):
                if self._records[previous]['state']=='consumed':self._records.pop(previous)
            if len(self._records)>=10000:raise ContinuationUnavailable('Suspension capacity exhausted')
            if any(r['old'].task_id==handle.task_id and r['state']!='consumed' for r in self._records.values()):raise ContinuationUnavailable('Suspension already retained')
            cap=Suspension(_KEY,self,handle.task_id);receipt=handle._receipt
            self._records[cap]={'old':handle,'engine':handle._child,'receipt':receipt,'receipt_values':_json(asdict(receipt)),'configuration':handle._configuration,'configuration_values':_json([asdict(handle.model),*handle._configuration[1:]]),'state':'pausing','failed':False,'new':None,'stamp':None,'notification_state':None,'notifications_transferred':False}
        try:self._allow(cap,'pause')
        except BaseException:
            with self._lock:self._records.pop(cap,None)
            raise
        return cap
    def confirm_pause(self,cap,paused_snapshot):
        rec=self._allow(cap,'pause');self._registry(cap,'pausing');old=rec['old'];child=old._child
        if (type(paused_snapshot) is not dict or str(paused_snapshot.get('id'))!=old.task_id
            or type(paused_snapshot.get('exploration_id')) is not int or paused_snapshot.get('exploration_id')!=old.exploration_id
            or paused_snapshot.get('paused') is not True or paused_snapshot.get('queued') is not False
            or paused_snapshot.get('status')!='running' or type(paused_snapshot.get('timeout_seconds')) is not int
            or paused_snapshot.get('timeout_seconds')!=0 or paused_snapshot.get('first_run_at') is not None or paused_snapshot.get('deadline_at') is not None):raise ContinuationUnavailable('Exact persisted paused snapshot required')
        if not old.token.cancelled or old.wait(0) is not True or old.unresolved or child is None or child.cleanup is None or child._intent_control is not None or child.cleanup._records:raise ContinuationUnavailable('Clean stopped generation required')
        if rec['state']!='pausing':raise ContinuationUnavailable('Pause confirmation cannot replay')
        r=rec['receipt']
        with self._pins[8](r.task_id,r.exploration_id):
            self._allow(cap,'pause')
            with self._pins[5].transaction() as tx:stamp=self._db.check(tx,r,paused=True)
            self._allow(cap,'pause')
        self._allow(cap,'pause')
        with self._pins[8](r.task_id,r.exploration_id):
            with self._pins[5].transaction() as tx:self._db.check(tx,r,paused=True,stamp=stamp)
        self._record(cap);self._registry(cap,'pausing')
        rec['notification_state']=_notification_state(child,stopped=True)
        rec['stamp']=stamp;rec['state']='paused'
    def prepare_resume(self,cap,paused_snapshot,new_token):
        from .bootstrap_handle import BootstrapHandle
        from ..manager import CancellationToken
        rec=self._allow(cap,'resume')
        if rec['state']!='paused' or type(new_token) is not CancellationToken or new_token is rec['old'].token or new_token.cancelled:raise ContinuationUnavailable('Fresh resume reservation required')
        self._registry(cap,'resuming',new_token=new_token)
        rec['state']='resuming';rec['new_token']=new_token
        try:
            task=deepcopy(dict(paused_snapshot))
            if (type(task.get('id')) not in (str,int) or str(task.get('id'))!=rec['old'].task_id
                or type(task.get('exploration_id')) is not int or task.get('exploration_id')!=rec['old'].exploration_id
                or task.get('timeout_seconds')!=0 or type(task.get('timeout_seconds')) is not int
                or task.get('paused') is not True or task.get('queued') is not False or task.get('status')!='running'
                or task.get('first_run_at') is not None or task.get('deadline_at') is not None):raise ContinuationUnavailable('Exact paused task required')
            handle=BootstrapHandle._from_continuation(self,cap,task,new_token,rec['old']);rec['new']=handle
            return handle
        except BaseException:self.quarantine(cap);raise
    def construction_receipt(self,cap,old,token):
        rec=self._record(cap)
        if rec['state']!='resuming' or rec['old'] is not old or rec.get('new_token') is not token:raise ContinuationUnavailable('Private resume construction required')
        return rec['receipt']
    def commit_resume(self,cap,new_handle):
        rec=self._allow(cap,'resume')
        if rec['state']!='resuming' or rec['new'] is not new_handle:raise ContinuationUnavailable('Exact new generation required')
        self._registry(cap,'resuming',new_token=new_handle.token)
        r=rec['receipt'];attempted=False
        try:
            with self._pins[8](r.task_id,r.exploration_id):
                with self._pins[5].transaction() as tx:
                    self._db.check(tx,r,paused=True,stamp=rec['stamp']);self._allow(cap,'resume')
                    self._db.check(tx,r,paused=True,stamp=rec['stamp']);self._allow(cap,'resume');attempted=True
                    self._db.resume(tx,r,rec['stamp']);self._allow(cap,'resume')
                self._allow(cap,'resume')
            self._allow(cap,'resume')
            task=self.manager.repository.get(r.task_id)
            if task is None or task.status!='running' or task.paused or task.timeout_seconds!=0 or task.first_run_at is not None or task.deadline_at is not None:raise ContinuationUnavailable('Resumed task changed')
            with self._pins[8](r.task_id,r.exploration_id):
                with self._pins[5].transaction() as tx:rec['resume_stamp']=self._db.check(tx,r,paused=False)
            self._record(cap)
            rec['state']='committed';return task
        except BaseException:
            if attempted:self.quarantine(cap);raise ContinuationUncertain('Resume may have committed; ownership retained') from None
            raise
    def confirm_installed(self,cap,new_handle):
        rec=self._allow(cap,'resume')
        self._registry(cap,'installed',new_handle=new_handle,new_token=new_handle.token)
        if rec['state']!='committed' or rec['new'] is not new_handle or not any(h is new_handle for h in self.runner.handles()):raise ContinuationUnavailable('Original resumed installation required')
        r=rec['receipt']
        with self._pins[8](r.task_id,r.exploration_id):
            with self._pins[5].transaction() as tx:self._db.check(tx,r,paused=False,stamp=rec['resume_stamp'])
        self._record(cap)
        self._registry(cap,'installed',new_handle=new_handle,new_token=new_handle.token)
        rec['state']='installed'
    def activate(self,cap,new_handle):
        rec=self._allow(cap,'resume')
        if rec['state'] not in ('installed','consumed') or rec['new'] is not new_handle or not self.manager.owns_generation(new_handle.task_id,new_handle.token,new_handle):raise ContinuationUnavailable('Installed continuation required')
        if rec['state']=='consumed':raise ContinuationUnavailable('Continuation activation cannot replay')
        r=rec['receipt']
        with self._pins[8](r.task_id,r.exploration_id):
            with self._pins[5].transaction() as tx:self._db.check(tx,r,paused=False,stamp=rec['resume_stamp'])
        self._record(cap)
        if not self.manager.owns_generation(new_handle.task_id,new_handle.token,new_handle):raise ContinuationUnavailable('Activation ownership changed')
        # Activation admission linearizes with sealing only after final SQL and
        # identity checks. Already admitted engine work still needs host cancel.
        with self._lock:
            if self._resume_sealed:raise ContinuationUnavailable('Resume admission sealed')
            if self._records.get(cap) is not rec or rec['failed'] or rec['state']!='installed' or rec['new'] is not new_handle:
                raise ContinuationUnavailable('Activation record changed')
            # Construction and bindings have not happened yet. This record must
            # not be pruned by another task's pause during a blocked factory.
            rec['state']='activating'
        return rec['receipt']
    def _transfer_notifications(self,cap,new_handle,new_engine):
        """Move queued notify() meaning only through the original continuation.

        This neither issues nor reconstructs a capability. The old engine stays
        cancelled; the replacement is inert and already belongs to the new
        installed manager generation. Any failure retains both references.
        """
        try:
            rec=self._allow(cap,'resume');old=rec['engine']
            if (rec['state']!='activating' or rec['new'] is not new_handle
                    or rec['notifications_transferred'] or new_handle._child is not new_engine
                    or new_engine is old or new_engine.token is not new_handle.token
                    or new_engine.scope!=old.scope
                    or not self.manager.owns_generation(new_handle.task_id,new_handle.token,new_handle)):
                raise ContinuationUnavailable('Exact new continuation engine required')
            state=rec['notification_state']
            if state is None or _notification_state(old,stopped=True)!=state:
                raise ContinuationUnavailable('Original paused notifications required')
            if _notification_state(new_engine,stopped=False)!=((),()):
                raise ContinuationUnavailable('Unused continuation notification queue required')
            ids,rows=state
            with new_engine._condition:
                # Recheck under the replacement lock before installing copies.
                if _notification_state(new_engine,stopped=False)!=((),()) or len(ids)>new_engine.max_events:
                    raise ContinuationUnavailable('Replacement notification capacity changed')
                new_engine._event_ids=set(ids)
                new_engine._pending_notifications={key:dict(kind=kind,intent_id=intent,detail=detail) for key,kind,intent,detail in rows}
                new_engine._events=list(new_engine._pending_notifications.values())
            self._allow(cap,'resume')
            if (_notification_state(old,stopped=True)!=state
                    or _notification_state(new_engine,stopped=False)!=state
                    or self._record(cap) is not rec
                    or not self.manager.owns_generation(new_handle.task_id,new_handle.token,new_handle)):
                raise ContinuationUnavailable('Continuation notifications changed during transfer')
            rec['notifications_transferred']=True
        except BaseException:
            self.quarantine(cap)
            raise
    def _finish_activation(self,cap,new_handle,new_engine):
        """Acknowledge a fully bound inert child, then enable its next tick.

        All policy callbacks run while the replacement remains prepared, so
        they cannot reenter executable engine work. The final transition has no
        host callbacks and follows manager -> owner lock order. A transferred
        queue alone never releases the original notification snapshot.
        """
        from ..manager import TaskManager
        rec=self._allow(cap,'resume')
        new_handle._owned()
        self._allow(cap,'resume')
        with self.manager._lock:
            if not TaskManager.owns_generation(self.manager,new_handle.task_id,new_handle.token,new_handle):
                raise ContinuationUnavailable('Activation ownership changed')
            with self._lock:
                if (self._resume_sealed or self._records.get(cap) is not rec or rec['failed']
                        or rec['state']!='activating' or rec['new'] is not new_handle
                        or not rec['notifications_transferred']):
                    raise ContinuationUnavailable('Activation acknowledgement unavailable')
                with new_handle._condition, new_engine._condition:
                    if (new_handle._phase!='prepared' or new_handle._child is not new_engine
                            or not new_handle._active or new_handle._thread!=get_ident()
                            or new_handle._unresolved or new_handle._completed
                            or _notification_state(new_engine,stopped=False)!=rec['notification_state']):
                        raise ContinuationUnavailable('Inert bound continuation changed')
                    # No callbacks or fallible binding operations after here.
                    new_handle._phase='engine'
                    rec['state']='consumed'

    def quarantine(self,cap):
        with self._lock:
            rec=self._records.get(cap)
            if rec is not None:rec['failed']=True;rec['state']='quarantined'
