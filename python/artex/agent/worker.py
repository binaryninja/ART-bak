"""One explicitly admitted worker intent, not an autonomous worker pool.

Source worker.Execute resumes exp<exploration>-worker-i<intent>; engine.go owns
claim and state routing. This adapter claims actual WorkerStore open/version CAS,
runs actual bounded ChatAgent with a host-created ToolRegistry, durably commits
JSONL, then CAS-settles ONLY this intent. 'done' means normal worker turn termination,
NOT goal/objective proof. max_turns/timeout/budget -> exhausted; errors -> blocked;
explicit worker_pause -> paused, task_pause -> open, planner_kill -> stopped.
No model-selected goal status, automatic retry, reopen/resume cleanup, worker-chat,
steering, goal proof, default tools, background thread or task completion.

Host current(scope,phase) and lifecycle_guard(scope) must share real run/config,
pause/archive/deletion and intent lifetime authority; phase is admit/run/settle.
Settlement may allow the same canceled generation while new admission is forbidden.
An explicit InflightCleanupOwner can instead reserve a single CREATED turn after
claim, commit it and settle that exact claim after execution freezes. Its opaque
capability is returned for engine-owned activity projection/finish; failures
quarantine ownership. This does not enable positive task timeout admission.
All intent writers must honor WorkerStore versions and the same barrier. Generic
ExplorationStore.claim_intent is NOT compatible. SQL and JSONL are not atomic:
any uncertainty leaves running ownership for explicit reconciliation, not replay.
After restart an already claimed/terminal intent is rejected, never executed twice.
Runtime/durable scope includes explicit run_id and revision alongside the full
generation_digest for native context tools. Older scoped receipts missing those
keys deliberately conflict; no automatic rewrite or replay migration is provided.
"""
from copy import deepcopy
from dataclasses import dataclass,asdict
from threading import RLock
import hashlib,json,re
from .chat import ChatAgent,ChatUnavailable
from .runtime import Limits,RunStopped,Tool,BoundedAgent
from ..db.core import Query,int64
from ..db.workers import WorkerStore,WorkerConflict
from ..tools.registry import ToolRegistry
from ..chat_dispatch import Cancellation
from ..sidequestion import Model

class WorkerUnresolved(RuntimeError):pass

@dataclass(frozen=True)
class WorkerScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    intent_id:int
    expected_version:int
    owner:str
    model:Model
    system:str
    @property
    def session_id(self):return f'exp{self.exploration_id}-worker-i{self.intent_id}'
    def identity(self,limits):
        value=asdict(self);value['limits']=asdict(limits)
        return {'role':'worker','task_id':self.task_id,'exploration_id':self.exploration_id,'run_id':self.run_id,'revision':self.revision,'intent_id':self.intent_id,'generation_digest':hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()}

@dataclass(frozen=True)
class WorkerOutcome:
    intent_id:int
    state:str
    version:int
    outcome:object
    cleanup:object=None

class _Transcript:
    def __init__(self,core,scope):
        self.core,self.scope=core,scope
        self.cleanup=core._cleanup;self.cap=None
    def begin(self,session,client,user):
        with self.core.guard(self.scope):
            self.core.check(self.scope,'run',claimed=True)
            turn=self.core.transcript.begin_scoped(session,client,user,self.scope.identity(self.core.limits))
            if self.cleanup is not None and turn.created:
                self.cap=self.cleanup.reserve('worker',self.scope,session,turn,
                    self.scope.identity(self.core.limits),worker_store=self.core.store)
            return turn
    def current(self,session,turn):
        with self.core.guard(self.scope):
            self.core.check(self.scope,'run',claimed=True)
            return self.core.transcript.current(session,turn)
    def commit(self,session,turn,result):
        if self.cap is not None:
            self.core._binding()
            persisted=self.cleanup.commit(self.cap,turn,result)
            self.core._binding()
            return persisted
        with self.core.guard(self.scope):
            self.core.check(self.scope,'settle',claimed=True)
            return self.core.transcript.commit(session,turn,result)

class IntentWorker:
    def __init__(self,*,store,transcript,current,lifecycle_guard,provider_resolver,registry_factory,authorize,limits=Limits(),cleanup=None,clock=None):
        self.cleanup=self._cleanup=None
        if not isinstance(store,WorkerStore):raise ValueError('Real WorkerStore required')
        if any(not callable(v) for v in (current,lifecycle_guard,provider_resolver,registry_factory,authorize)):raise ValueError('Explicit worker authority and adapters required')
        if any(not callable(getattr(transcript,n,None)) for n in ('begin_scoped','check_scoped','current','commit')):raise ValueError('Durable scoped transcript required')
        if clock is not None and not callable(clock):raise ValueError('Explicit runtime clock must be callable')
        self.clock=self._clock=clock
        self.store,self.transcript,self.current,self.guard=store,transcript,current,lifecycle_guard
        self._store,self._executor=store,store.executor
        self.provider,self.registry_factory,self.authorize=provider_resolver,registry_factory,authorize
        self.limits=limits;self._lock=RLock();self._active={};self._started=False
        if cleanup is not None:self.bind_cleanup(cleanup)
    def bind_cleanup(self,owner):
        """Install the original cleanup owner once, before any worker admission."""
        from .inflight_cleanup import InflightCleanupOwner
        with self._lock:
            self._binding()
            if self._started or self._active or self._cleanup is not None:
                raise WorkerConflict('Cleanup binding requires an unused worker')
            if type(owner) is not InflightCleanupOwner or owner.worker_store is not self.store or owner.transcript is not self.transcript:
                raise ValueError('Exact cleanup owner with original worker resources required')
            self.cleanup=self._cleanup=owner
    def _binding(self):
        if self.clock is not self._clock or self.cleanup is not self._cleanup or self.store is not self._store or self.store.executor is not self._executor:raise WorkerConflict('Worker owning resource changed')
    def check(self,scope,phase,*,claimed=False):
        self._binding()
        if not isinstance(scope,WorkerScope):raise ValueError('WorkerScope required')
        for id in (scope.task_id,scope.exploration_id,scope.run_id,scope.intent_id):
            if int64(id,'worker identity')<=0:raise ValueError('Positive identity required')
        if type(scope.expected_version) is not int or not 0<=scope.expected_version<2**31-2:raise ValueError('Invalid version')
        if (scope.task_id,scope.exploration_id)!=(self.store.task_id,self.store.exploration_id):raise WorkerConflict('Worker scope mismatch')
        if not isinstance(scope.owner,str) or not scope.owner or len(scope.owner)>128 or not isinstance(scope.revision,str) or not scope.revision or not isinstance(scope.model,Model) or not isinstance(scope.system,str):raise ValueError('Explicit worker configuration required')
        if self.current(scope,phase) is not True:raise RunStopped('stale_worker_generation')
        self._binding()
        if (scope.task_id,scope.exploration_id)!=(self.store.task_id,self.store.exploration_id):raise WorkerConflict('Worker scope mismatch')
        record=self.store.get(scope.intent_id)
        if record is None:raise WorkerConflict('Intent missing')
        expected=scope.expected_version+(1 if claimed else 0)
        if record.version!=expected or record.node.state!=('running' if claimed else 'open') or (claimed and record.node.owner!=scope.owner):raise WorkerConflict('Intent generation or ownership changed')
        return record
    def cancel(self,intent_id,reason='worker_pause'):
        if reason not in ('worker_pause','task_pause','planner_kill'):raise ValueError('Unsupported cancellation cause')
        with self._lock:
            active=self._active.get(intent_id)
            if active is None or active[2]:return False
            if not active[0].cancelled:active[1]=reason
            active[0].cancel();return True
    def run(self,scope,client_id):
        if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',client_id):raise ValueError('Invalid worker client ID')
        token=Cancellation();claimed=False;transcript=None
        with self.guard(scope):
            record=self.check(scope,'admit')
            with self._lock:
                if scope.intent_id in self._active:raise WorkerConflict('Intent already active')
                self._active[scope.intent_id]=[token,None,False]
                self._started=True
        try:
            # Preflight the explicit registry and current provider before SQL claim.
            registry=self.registry_factory(scope,record)
            if not isinstance(registry,ToolRegistry) or registry.agent_key!='worker':raise ChatUnavailable('Worker registry unavailable')
            binding=self.provider(scope,scope.model)
            self._binding()
            from .chat import ChatBinding
            if not scope.model.identity or not isinstance(self.limits,Limits):raise ValueError('Identified model and runtime limits required')
            method='stream' if scope.model.streaming else 'complete'
            if not isinstance(binding,ChatBinding) or binding.model!=scope.model or not callable(getattr(binding.provider,method,None)):raise ChatUnavailable('Worker provider unavailable')
            from .streaming import StreamingCompletion
            BoundedAgent(StreamingCompletion(binding.provider,check=lambda:None) if scope.model.streaming else binding.provider,gate=self.authorize,tools=registry.tools(),limits=self.limits)
            scope.system.encode('utf-8')
            payload=record.node.payload
            if isinstance(payload,str):payload=json.loads(payload)
            message='Execute only this admitted intent, persist observations through authorized tools, then stop.\n'+json.dumps({'intent_id':scope.intent_id,'payload':payload},ensure_ascii=False,allow_nan=False)
            with self.guard(scope):
                self.check(scope,'admit');token.check()
                self.transcript.check_scoped(scope.session_id,client_id,{'role':'user','content':[{'type':'text','text':message}]},scope.identity(self.limits))
                with self.store.executor.transaction() as tx:
                    row=tx.fetch_one(Query("SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL FOR UPDATE",(scope.task_id,scope.exploration_id)))
                    if row is None or tuple(row)!=(scope.task_id,):raise WorkerConflict('Task not runnable')
                    bound=WorkerStore(tx,scope.task_id,scope.exploration_id,lifetime_guard=self.store.lifetime_guard)
                    claimed=True  # SQL/commit may become uncertain once claim is attempted.
                    if not bound.claim(scope.intent_id,scope.owner,scope.expected_version):raise WorkerConflict('Concurrent intent claim')
                claimed=True
            def provider(model):
                token.check()
                with self.guard(scope):
                    self.check(scope,'run',claimed=True)
                    binding=self.provider(scope,model)
                    self.check(scope,'run',claimed=True)
                    return binding
            def gate(context,action):
                with self.guard(scope):
                    self.check(scope,'run',claimed=True)
                    grant=self.authorize(context,action)
                    self.check(scope,'run',claimed=True);return grant
            guarded=[]
            for tool in registry.tools():
                def execute(context,args,tool=tool):
                    with self.guard(scope):
                        self.check(scope,'run',claimed=True);context.check()
                        return tool.callback(context,args)
                guarded.append(Tool(tool.name,tool.description,tool.input_schema,execute,tool.validate))
            transcript=_Transcript(self,scope)
            self._binding()
            agent=ChatAgent(transcript,provider,gate=gate,tools=guarded,limits=self.limits,clock=self._clock)
            outcome=agent.run(scope.session_id,client_id,message,model=scope.model,system=scope.system,metadata=scope.identity(self.limits),token=token)
            if transcript.cap is not None:
                outcome=transcript.cleanup.canonical_outcome(transcript.cap,outcome)
                self._binding()
            if not outcome.persisted or outcome.stale or outcome.result is None:raise WorkerUnresolved('Worker transcript outcome unresolved')
            reason=outcome.result.reason
            def terminal_state():
                # Linearize cancellation against the final SQL decision. After
                # this flag, cancel returns False rather than promising a pause.
                with self._lock:
                    active=self._active[scope.intent_id]
                    active[2]=True
                    cancel_reason=active[1]
                    if token.cancelled:
                        state={'worker_pause':'paused','task_pause':'open','planner_kill':'stopped'}.get(cancel_reason)
                        if state is None:raise WorkerUnresolved('Cancellation cause unavailable')
                    elif reason=='completed':state='done'
                    elif reason in ('max_turns','timeout','token_budget','tool_budget','task_deadline_expired'):state='exhausted'
                    else:state='blocked'
                    return state,cancel_reason if token.cancelled else None
            if transcript.cap is not None:
                # This capability owns only the exact already-claimed job and
                # canonical turn; it grants no execution or admission authority.
                self._binding()
                state,cancel_reason=terminal_state()
                if not transcript.cleanup.worker_settle(transcript.cap,self.store,state,cancellation_reason=cancel_reason):
                    raise WorkerUnresolved('Worker cleanup settlement unresolved')
                self._binding()
            else:
                with self.guard(scope):
                    self.check(scope,'settle',claimed=True)
                    state,_=terminal_state()
                    if not self.store.transition(scope.intent_id,'running',state,scope.expected_version+1):raise WorkerUnresolved('Worker settlement compare-and-set failed')
            return WorkerOutcome(scope.intent_id,state,scope.expected_version+2,outcome,transcript.cap)
        except BaseException as error:
            if transcript is not None and transcript.cap is not None:
                transcript.cleanup.quarantine(transcript.cap)
            if claimed:raise WorkerUnresolved('Worker effects or settlement require reconciliation; do not retry') from None
            raise
        finally:
            with self._lock:self._active.pop(scope.intent_id,None)
