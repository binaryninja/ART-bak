"""Exact one-turn cleanup after execution revocation; never new execution.

Opt-in host policy, canonical CREATED receipt and original resources are mandatory.
Only commit, predeclared worker CAS and receipt-bound activity may finish. Unknown
writes retain manager ownership. No recovery, transfer or positive timeout gates.
"""
from dataclasses import dataclass,asdict,replace
from copy import deepcopy
from threading import RLock,get_ident
import hashlib,json
from .chat import TranscriptTurn,ChatOutcome
from .runtime import RunResult
from ..chat_transcripts import ChatTranscriptAdapter,_scope_identity
from ..transcripts import TranscriptStore
from ..db.workers import WorkerStore
from ..db.core import Query

class CleanupUnavailable(RuntimeError):pass
class CleanupUncertain(RuntimeError):pass

@dataclass(frozen=True,eq=False)
class CleanupTurn:
    owner:object
    task_id:str
    turn_id:str

@dataclass(frozen=True)
class CleanupProposal:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    role:str
    session_id:str
    turn_id:str
    identity_digest:str
    operation:str

class InflightCleanupOwner:
    def __init__(self,manager,handle,token,scope,*,transcript,worker_store,projector,current,configuration_current,lifecycle_guard,authorize):
        from ..manager import TaskManager
        from .bootstrap_handle import BootstrapHandle
        from .activity_projection import ActivityProjector
        if type(manager) is not TaskManager or type(handle) is not BootstrapHandle or handle.manager is not manager or handle.token is not token:raise ValueError('Actual installed cleanup owner required')
        if type(transcript) is not ChatTranscriptAdapter or type(transcript.store) is not TranscriptStore or type(worker_store) is not WorkerStore:raise ValueError('Canonical original cleanup resources required')
        if projector is not None and type(projector) is not ActivityProjector:raise ValueError('Actual predeclared projector required')
        if any(not callable(v) for v in (current,configuration_current,lifecycle_guard,authorize)):raise ValueError('Explicit cleanup authority required')
        if worker_store.executor is not handle.executor or (projector is not None and (projector.executor is not handle.executor or projector.transcript is not transcript)):raise ValueError('Shared cleanup resources required')
        self.manager,self.handle,self.token,self.scope=manager,handle,token,scope
        self.transcript,self.worker_store,self.projector=transcript,worker_store,projector
        self.current,self.configuration_current,self.guard,self.authorize=current,configuration_current,lifecycle_guard,authorize
        self._pins=(manager,handle,token,scope,transcript,worker_store,projector,current,configuration_current,lifecycle_guard,authorize,handle.executor)
        self._t=(transcript.store,transcript.store.root,transcript.store.max_bytes,transcript.agent_id,transcript.recovery_guard)
        self._engine=handle._child
        self._components=(self._engine.planner,self._engine.worker) if self._engine is not None else (None,None)
        self._worker=(worker_store.lifetime_guard,worker_store.task_id,worker_store.exploration_id)
        self._scope=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision)
        self._records={};self._lock=RLock()
    def _binding(self):
        if any(a is not b for a,b in zip((self.manager,self.handle,self.token,self.scope,self.transcript,self.worker_store,self.projector,self.current,self.configuration_current,self.guard,self.authorize,self.handle.executor),self._pins)):raise CleanupUnavailable('Cleanup binding changed')
        t=self.transcript
        if t.store is not self._t[0] or (t.store.root,t.store.max_bytes,t.agent_id,t.recovery_guard)!=self._t[1:] or self.worker_store.executor is not self._pins[11]:raise CleanupUnavailable('Cleanup storage changed')
        if (self.scope.task_id,self.scope.exploration_id,self.scope.run_id,self.scope.revision)!=self._scope or (self.worker_store.lifetime_guard,self.worker_store.task_id,self.worker_store.exploration_id)!=self._worker:raise CleanupUnavailable('Cleanup scope or worker guard changed')
        if self.handle._child is not self._engine or self._engine is None or (self._engine.planner,self._engine.worker)!=self._components or self._engine.worker.store is not self.worker_store or self._engine.planner.transcript is not self.transcript or self._engine.worker.transcript is not self.transcript:raise CleanupUnavailable('Original engine components changed')
        if self.handle.scope!=self.scope or self.handle.validate_prepared_receipt(self.handle._receipt) is not True:raise CleanupUnavailable('Prepared cleanup generation changed')
    def _record(self,cap):
        self._binding()
        with self._lock:record=self._records.get(cap)
        if type(cap) is not CleanupTurn or cap.owner is not self or record is None or record['thread']!=get_ident() or record['failed']:raise CleanupUnavailable('Exact active cleanup turn required')
        return record
    def _check(self,cap,operation):
        rec=self._record(cap)
        if not self.manager.validate_inflight_cleanup(self,cap):raise CleanupUnavailable('Cleanup registry ownership changed')
        if self.configuration_current(self.scope) is not True:raise CleanupUnavailable('Cleanup configuration revoked')
        self._binding()
        p=CleanupProposal(*self._scope,rec['role'],rec['session'],cap.turn_id,rec['digest'],operation)
        if self.authorize(p) is not True:raise CleanupUnavailable('Cleanup operation denied')
        if self.configuration_current(self.scope) is not True:raise CleanupUnavailable('Cleanup configuration revoked by grant')
        self._binding()
        if not self.manager.validate_inflight_cleanup(self,cap):raise CleanupUnavailable('Cleanup ownership changed')
        r=self.handle._receipt
        rows=self._pins[11].fetch_all(Query("SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND status='running' AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1)",(r.task_id,r.exploration_id)))
        if rows!=[(r.task_id,)]:raise CleanupUnavailable('Cleanup task unavailable')
        rows=self._pins[11].fetch_all(Query("SELECT task_id FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2 AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared'",(r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
        if rows!=[(r.task_id,)]:raise CleanupUnavailable('Cleanup durable generation changed')
        if self.configuration_current(self.scope) is not True or self.authorize(p) is not True:raise CleanupUnavailable('Cleanup final grant revoked')
        if self.configuration_current(self.scope) is not True:raise CleanupUnavailable('Cleanup configuration revoked by grant')
        self._binding()
        if not self.manager.validate_inflight_cleanup(self,cap):raise CleanupUnavailable('Cleanup ownership changed')
        return rec
    def validate_reservation(self,cap):
        rec=self._record(cap)
        with self.handle._condition:
            if self.handle._child is not self._engine or self._engine is None or self.handle._phase!='engine' or not self.handle._active or self.handle._thread!=get_ident():raise CleanupUnavailable('Active installed outer engine required')
        with self._engine._condition:
            if not self._engine._active or self._engine._thread!=get_ident():raise CleanupUnavailable('Active owned child required')
        if self.current(rec['scope']) is not True:raise CleanupUnavailable('Execution required to reserve cleanup')
        if self.transcript.current(rec['session'],rec['turn']) is not True:raise CleanupUnavailable('Pending canonical turn required')
        self._binding();return True
    def reserve(self,role,scope,session,turn,identity,*,worker_store=None):
        self._binding();identity=_scope_identity(identity)
        if role not in ('planner','worker') or type(turn) is not TranscriptTurn or not turn.created or turn.finalized:raise CleanupUnavailable('Created pending execution turn required')
        if tuple(getattr(scope,k) for k in ('task_id','exploration_id','run_id','revision'))!=self._scope or any(identity.get(k)!=v for k,v in zip(('task_id','exploration_id','run_id','revision'),self._scope)) or identity.get('role')!=role or session!=scope.session_id:raise CleanupUnavailable('Foreign cleanup scope')
        if role=='worker' and worker_store is not self.worker_store:raise CleanupUnavailable('Original worker store required')
        if role=='planner' and worker_store is not None:raise CleanupUnavailable('Planner cannot reserve worker mutation')
        cap=CleanupTurn(self,str(scope.task_id),turn.id)
        rec=dict(thread=get_ident(),scope=deepcopy(scope),role=role,session=session,turn=deepcopy(turn),identity=identity,digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),stage='pending',failed=False,evidence=None,operation=None)
        with self._lock:self._records[cap]=rec
        try:
            self.manager.reserve_inflight_cleanup(self,cap)
            self._check(cap,'reserve');self.validate_reservation(cap)
            if role=='worker':
                row=self.worker_store.get(scope.intent_id)
                if row is None or row.version!=scope.expected_version+1 or row.node.state!='running' or row.node.owner!=scope.owner:raise CleanupUnavailable('Exact claimed worker required')
                with self._engine.worker._lock:
                    active=self._engine.worker._active.get(scope.intent_id)
                    if active is None:raise CleanupUnavailable('Actual active worker required')
                    rec['worker_token']=active[0]
            return cap
        except BaseException:
            try:self.quarantine(cap)
            except BaseException:pass
            raise
    def commit(self,cap,turn,result):
        rec=self._record(cap)
        if rec['stage']!='pending' or turn!=rec['turn'] or type(result) is not RunResult:raise CleanupUnavailable('Exact pending commit required')
        try:
            with self.guard(self.scope):
                self._check(cap,'commit')
                if self.transcript.current(rec['session'],rec['turn']) is not True:raise CleanupUnavailable('Pending transcript changed')
                if not self.manager.owns_generation(cap.task_id,self.token,self.handle):
                    result=replace(result,reason='cancelled')
                saved=self.transcript.commit(rec['session'],rec['turn'],result)
                if saved is not True:raise CleanupUncertain('Canonical commit unconfirmed')
                evidence=self.transcript.finalized_scoped_result(rec['session'],cap.turn_id,rec['identity'])
                if evidence is None:raise CleanupUncertain('Canonical result missing')
                rec['evidence']=evidence;rec['stage']='committed';self._check(cap,'commit')
            self._check(cap,'commit');return True
        except BaseException:self.quarantine(cap);raise CleanupUncertain('Cleanup commit unconfirmed') from None
    def _canonical(self,cap):
        rec=self._record(cap)
        if rec['evidence'] is None or self.transcript.finalized_scoped_result(rec['session'],cap.turn_id,rec['identity'])!=rec['evidence']:raise CleanupUnavailable('Canonical cleanup evidence changed')
        self._binding();return rec['evidence']
    def canonical_outcome(self,cap,outcome):
        rec=self._record(cap)
        if type(outcome) is not ChatOutcome or outcome.turn.id!=cap.turn_id or outcome.persisted is not True or rec['stage']!='committed':raise CleanupUnavailable('Exact committed outcome required')
        self._check(cap,'outcome')
        if self.transcript.finalized_scoped_result(rec['session'],cap.turn_id,rec['identity'])!=rec['evidence']:raise CleanupUnavailable('Canonical cleanup evidence changed')
        return replace(outcome,result=deepcopy(rec['evidence'].result))
    def worker_settle(self,cap,store,state,*,cancellation_reason=None):
        rec=self._record(cap);scope=rec['scope']
        if rec['role']!='worker' or store is not self.worker_store or rec['stage']!='committed':raise CleanupUnavailable('Exact committed worker cleanup required')
        reason=rec['evidence'].result.reason
        with self._engine.worker._lock:
            active=self._engine.worker._active.get(scope.intent_id)
            if active is None or active[0] is not rec.get('worker_token') or not active[2]:raise CleanupUnavailable('Worker settlement not owned')
            actual_reason=active[1] if active[0].cancelled else None
            if cancellation_reason!=actual_reason:raise CleanupUnavailable('Worker cancellation cause changed')
        expected=({'worker_pause':'paused','task_pause':'open','planner_kill':'stopped'}.get(cancellation_reason) if cancellation_reason is not None else 'done' if reason=='completed' else 'exhausted' if reason in ('max_turns','timeout','token_budget','tool_budget','task_deadline_expired') else 'blocked')
        if state!=expected or state is None:raise CleanupUnavailable('Worker terminal mapping changed')
        try:
            # WorkerStore owns its guard; do not nest a potentially nonreentrant one.
            self._check(cap,'worker_settle')
            row=store.get(scope.intent_id)
            if row is None or row.version!=scope.expected_version+1 or row.node.owner!=scope.owner or row.node.state!='running':raise CleanupUnavailable('Claim version changed')
            self._check(cap,'worker_settle');self._canonical(cap)
            if store.transition(scope.intent_id,'running',state,scope.expected_version+1) is not True:raise CleanupUncertain('Worker cleanup CAS failed')
            rec['stage']='worker_settled';self._check(cap,'worker_settle');self._canonical(cap)
            after=store.get(scope.intent_id)
            if after is None or after.version!=scope.expected_version+2 or after.node.state!=state or after.node.owner!=scope.owner:raise CleanupUncertain('Worker cleanup result changed')
            self._check(cap,'worker_settle');self._canonical(cap);return True
        except BaseException:self.quarantine(cap);raise CleanupUncertain('Worker cleanup unconfirmed') from None
    def projection_current(self,cap,projector,role,scope,outcome,identity):
        rec=self._record(cap)
        if rec['operation']!='projection' or projector is not self.projector or role!=rec['role'] or scope!=rec['scope'] or _scope_identity(identity)!=rec['identity'] or outcome.turn.id!=cap.turn_id or outcome.persisted is not True:raise CleanupUnavailable('Exact pending projection required')
        self._check(cap,'project')
        if self.transcript.finalized_scoped_result(rec['session'],cap.turn_id,rec['identity'])!=rec['evidence']:raise CleanupUnavailable('Projection evidence changed')
        return True
    def project(self,cap,projector,outcome,identity):
        rec=self._record(cap)
        if projector is not self.projector or projector is None or rec['stage']!=('worker_settled' if rec['role']=='worker' else 'committed'):raise CleanupUnavailable('Original projection stage required')
        rec['operation']='projection'
        try:
            result=projector.project(rec['role'],rec['scope'],outcome,identity,cleanup=cap)
            rec['stage']='projected';return result
        except BaseException:self.quarantine(cap);raise CleanupUncertain('Cleanup projection unconfirmed') from None
        finally:rec['operation']=None
    def finish(self,cap):
        rec=self._record(cap);expected='projected' if self.projector is not None else 'worker_settled' if rec['role']=='worker' else 'committed'
        if rec['stage']!=expected:raise CleanupUnavailable('Cleanup stages incomplete')
        self._check(cap,'finish');self._canonical(cap);self.manager.finish_inflight_cleanup(self,cap)
        with self._lock:del self._records[cap]
    def quarantine(self,cap):
        with self._lock:
            rec=self._records.get(cap)
            if rec is None or cap.owner is not self:raise CleanupUnavailable('Unknown cleanup capability')
            rec['failed']=True
        self.manager.quarantine_inflight_cleanup(self,cap)
