"""Bounded task-main human conversation, not autonomous planner admission.

Source mainagent.go/server.go: paused tasks may chat; segment zero uses
exp<exploration>-main and later segments exp<exploration>-main-s<seg>. One turn
per task, independent of planner/worker busy state. This core actually executes
ChatAgent's bounded permission-gated loop and requires durable transcript CAS.
Durable admission binds the unchanged client ID to task/exploration/segment/run/
revision/model/prompt identity; changed-scope duplicates are rejected, not replayed.
No default tools, shell, providers, task revival, hints/goals mutation, fallback
rule replies, activity/SSE projection or HTTP202 is supplied.

Host resolve(task_id,segment) returns an explicitly authorized TaskChatScope.
lifecycle_guard(scope) must serialize with deletion/archive/segment-clear and
run/revision replacement; current(scope) must check all fields against current
owned state. Both must share real host authority, not a no-op/test mutex. Pause
may cancel an active turn but does not prohibit a later turn while paused. The
existing AgentHost has no main-segment/revision authority: no implicit adapter.
Guards protect transcript admission/commit and checks at model/tool boundaries;
callbacks already in flight cannot be undone. Host must cancel AND drain before
resource deletion. SQL task state and JSONL are not one atomic transaction.
"""
from dataclasses import dataclass,replace,asdict
from copy import deepcopy
import hashlib,json
from threading import RLock,Condition,get_ident
from contextlib import contextmanager
import math,time
from .agent.chat import ChatAgent,ChatOutcome,ChatUnavailable,ChatBusy,ChatBinding,ChatPersistenceError
from .agent.runtime import Limits,RunStopped,BoundedAgent
from .chat_dispatch import Cancellation
from .db.core import int64
from .sidequestion import Model

@dataclass(frozen=True)
class TaskChatScope:
    task_id:int
    exploration_id:int
    segment:int
    run_id:int
    revision:str
    model:Model
    system:str
    @property
    def session_id(self):
        return f'exp{self.exploration_id}-main'+(f'-s{self.segment}' if self.segment else '')

def _scope(value,task_id,segment):
    if not isinstance(value,TaskChatScope):raise ChatUnavailable('Authorized task chat scope unavailable')
    for id in (value.task_id,value.exploration_id,value.run_id):
        int64(id,'scope ID')
        if id<=0:raise ValueError('Positive scope IDs required')
    if value.task_id!=task_id or (segment is not None and value.segment!=segment):raise ChatUnavailable('Task chat scope mismatch')
    if type(value.segment) is not int or not 0<=value.segment<2**31:raise ValueError('Invalid main segment')
    if not isinstance(value.revision,str) or not value.revision or len(value.revision)>256:raise ValueError('Explicit scope revision required')
    if not isinstance(value.model,Model) or not isinstance(value.system,str):raise ValueError('Explicit model and system required')
    return value

def scope_identity(scope):
    """Durable exact admission identity, separate from the caller's client ID."""
    return {'task_id':scope.task_id,'exploration_id':scope.exploration_id,
            'segment':scope.segment,'run_id':scope.run_id,'revision':scope.revision,
            'model':asdict(scope.model),
            'system_sha256':hashlib.sha256(scope.system.encode('utf-8')).hexdigest()}

class _ScopedTranscript:
    def __init__(self,core,scope,authorize=lambda:None):self.core,self.scope,self.authorize=core,scope,authorize
    def begin(self,session,client,user):
        with self.core.guard(self.scope):
            self.core._check(self.scope,session);self.authorize()
            return self.core.transcript.begin_scoped(session,client,user,scope_identity(self.scope))
    def current(self,session,turn):
        with self.core.guard(self.scope):
            self.core._check(self.scope,session);self.authorize()
            return self.core.transcript.current(session,turn)
    def commit(self,session,turn,result):
        with self.core.guard(self.scope):
            self.core._check(self.scope,session);self.authorize()
            return self.core.transcript.commit(session,turn,result)

@dataclass(frozen=True)
class TaskChatDrain:
    active_invocations:int
    unresolved:int
    sealed:bool
    @property
    def drained(self):return self.sealed and self.active_invocations==0 and self.unresolved==0

@dataclass(eq=False)
class _ChatInvocation:
    task_id:int
    kind:str
    thread:int|None
    scope:object=None
    started:bool=False
    token:object=None
    authorize:object=None

class TaskChatCore:
    def __init__(self,*,resolve,current,lifecycle_guard,transcript,provider_resolver,gate,tools=(),limits=Limits(),capture=None):
        if any(not callable(value) for value in (resolve,current,lifecycle_guard,provider_resolver,gate)):raise ValueError('Explicit task lifecycle/provider/permission adapters required')
        if any(not callable(getattr(transcript,name,None)) for name in ('begin','current','commit')):raise ValueError('Durable transcript adapter required')
        self.resolve,self.current,self.guard=resolve,current,lifecycle_guard
        self.transcript,self.provider_resolver,self.gate=transcript,provider_resolver,gate
        self.tools,self.limits,self.capture=tuple(tools),limits,capture
        self._lock=RLock();self._condition=Condition(self._lock);self._active={};self._unresolved=set()
        self._invocations=set();self._sealed=False;self._cancel_on_start=False
    def _enter(self,task_id,kind,scope=None):
        with self._condition:
            if self._sealed:raise ChatUnavailable('Task chat admission sealed')
            receipt=_ChatInvocation(task_id,kind,None if kind=='queued' else get_ident(),scope)
            self._invocations.add(receipt);return receipt
    def _leave(self,receipt):
        with self._condition:
            self._invocations.discard(receipt);self._condition.notify_all()
    def _reserve_queued(self,scope,authorize=None):
        if type(scope) is not TaskChatScope:raise ChatUnavailable('Exact task scope required')
        receipt=self._enter(scope.task_id,'queued',deepcopy(scope));receipt.authorize=authorize
        return receipt
    def _run_queued(self,receipt,client_id,message,token):
        with self._condition:
            if receipt not in self._invocations or receipt.kind!='queued' or receipt.started:raise ChatUnavailable('Original queued invocation required')
            receipt.started=True;receipt.thread=get_ident()
        # The same queue worker owns terminal projection too, until Future settlement.
        return self._run_impl(receipt.task_id,client_id,message,scope=receipt.scope,segment=receipt.scope.segment,token=token,_receipt=receipt)
    @contextmanager
    def _project_queued(self,receipt):
        with self._condition:
            if receipt not in self._invocations or receipt.kind!='queued':
                raise ChatUnavailable('Original queued invocation required')
            previous=receipt.thread;receipt.thread=get_ident()
        try:yield
        finally:
            with self._condition:
                receipt.thread=previous;self._condition.notify_all()
    def _bind_token(self,receipt,token):
        with self._condition:
            if receipt not in self._invocations:raise ChatUnavailable('Task invocation unavailable')
            receipt.token=token;cancel=self._cancel_on_start
        if cancel:token.cancel()
    def seal(self,*,cancel=False):
        if type(cancel) is not bool:raise ValueError('Boolean cancellation required')
        with self._condition:
            self._sealed=True;self._cancel_on_start=self._cancel_on_start or cancel
            tokens=[(r.task_id,r.token) for r in self._invocations if r.token is not None] if cancel else []
            callbacks=[(_ChatInvocation(task_id,'cancel',get_ident()),token) for task_id,token in tokens]
            self._invocations.update(r for r,t in callbacks)
            self._condition.notify_all()
        try:
            for receipt,token in callbacks:token.cancel()
        finally:
            for receipt,token in callbacks:self._leave(receipt)
        return True
    def snapshot(self):
        with self._condition:return TaskChatDrain(len(self._invocations),len(self._unresolved),self._sealed)
    def wait(self,timeout=None):
        if timeout is not None and (type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0):raise ValueError('Finite drain timeout required')
        deadline=None if timeout is None else time.monotonic()+timeout
        with self._condition:
            while self._invocations:
                if any(r.thread==get_ident() for r in self._invocations):break
                remaining=None if deadline is None else deadline-time.monotonic()
                if remaining is not None and remaining<=0:break
                self._condition.wait(remaining)
            return TaskChatDrain(len(self._invocations),len(self._unresolved),self._sealed)
    def close(self,*,cancel=False,timeout=None):
        self.seal(cancel=cancel);return self.wait(timeout)
    def _check(self,scope,session):
        if session!=scope.session_id or self.current(scope) is not True:raise RunStopped('stale_task_chat')
    def running(self,task_id):
        with self._lock:return task_id in self._active or task_id in self._unresolved or any(r.task_id==task_id and r.kind!='prepare' for r in self._invocations)
    def cancel(self,task_id):
        with self._condition:
            token=self._active.get(task_id)
            if token is None:return False
            receipt=_ChatInvocation(task_id,'cancel',get_ident());self._invocations.add(receipt)
        try:token.cancel();return True
        finally:self._leave(receipt)
    def prepare(self,task_id,*,segment=None):
        receipt=self._enter(task_id,'prepare')
        try:return self._prepare_impl(task_id,segment=segment)
        finally:self._leave(receipt)
    def _prepare_impl(self,task_id,*,segment=None):
        int64(task_id,'task ID')
        if task_id<=0 or (segment is not None and (type(segment) is not int or not 0<=segment<2**31)):raise ValueError('Invalid task/segment')
        scope=deepcopy(_scope(self.resolve(task_id,segment),task_id,segment))
        with self.guard(scope):
            self._check(scope,scope.session_id)
            if not isinstance(self.limits,Limits):raise ValueError('Invalid task chat limits')
            if not scope.model.identity:raise ValueError('Identified model required')
            scope.system.encode('utf-8')
            binding=self.provider_resolver(scope,scope.model)
            if not isinstance(binding,ChatBinding) or binding.model!=scope.model or not callable(getattr(binding.provider,'stream' if scope.model.streaming else 'complete',None)):
                raise ChatUnavailable('Current task model unavailable')
            from .agent.streaming import StreamingCompletion
            preflight_provider=StreamingCompletion(binding.provider,check=lambda:None) if scope.model.streaming else binding.provider
            BoundedAgent(preflight_provider,gate=self.gate,tools=self.tools,limits=self.limits)
            ChatAgent(self.transcript,lambda model:binding,gate=self.gate,tools=self.tools,limits=self.limits,capture=self.capture)
            self._check(scope,scope.session_id)
        return scope
    def run(self,task_id,client_id,message,*,segment=None,scope=None,token=None):
        receipt=self._enter(task_id,'run')
        try:return self._run_impl(task_id,client_id,message,segment=segment,scope=scope,token=token,_receipt=receipt)
        finally:self._leave(receipt)
    def _run_impl(self,task_id,client_id,message,*,segment=None,scope=None,token=None,_receipt):
        if not callable(getattr(self.transcript,'begin_scoped',None)):
            raise ChatUnavailable('Durable scoped transcript admission required')
        scope=self._prepare_impl(task_id,segment=segment) if scope is None else deepcopy(_scope(scope,task_id,segment))
        token=Cancellation() if token is None else token
        if type(getattr(token,'cancelled',None)) is not bool or not all(callable(getattr(token,n,None)) for n in ('check','cancel')):
            raise ValueError('Cooperative cancellation token required')
        def request_check():
            if _receipt.authorize is not None and _receipt.authorize() is not True:
                raise PermissionError("Task request denied")
        request_check()
        self._bind_token(_receipt,token)
        token.check()
        # Register under the same external barrier that deletion/clear must use.
        with self.guard(scope):
            self._check(scope,scope.session_id)
            with self._lock:
                if task_id in self._active or task_id in self._unresolved:raise ChatBusy('Task chat active or requires reconciliation')
                self._active[task_id]=token
        admitted=False
        core=self
        class Transcript(_ScopedTranscript):
            def begin(self,session,client,user):
                nonlocal admitted
                # A begin exception may have followed durable admission.
                admitted=True
                return super().begin(session,client,user)
        def provider(model):
            token.check()
            with self.guard(scope):
                self._check(scope,scope.session_id)
                binding=self.provider_resolver(scope,model)
                request_check();return binding
        def gate(context,action):
            token.check()
            with self.guard(scope):
                self._check(scope,scope.session_id)
                grant=self.gate(context,action)
                request_check();return grant
        try:
            agent=ChatAgent(Transcript(self,scope,request_check),provider,gate=gate,tools=self.tools,limits=self.limits,capture=self.capture)
            outcome=agent.run(scope.session_id,client_id,message,model=scope.model,system=scope.system,
                              metadata={'task_id':task_id,'exploration_id':scope.exploration_id,'main_seg':scope.segment,'task_run_id':scope.run_id,'task_revision':scope.revision},token=token)
            if not isinstance(outcome,ChatOutcome) or not outcome.persisted or outcome.stale:
                with self._lock:self._unresolved.add(task_id)
            if outcome.reused and outcome.persisted:
                loader=getattr(self.transcript,'finalized_result',None)
                if not callable(loader):raise ChatPersistenceError()
                with self.guard(scope):
                    self._check(scope,scope.session_id)
                    result=loader(scope.session_id,outcome.turn)
                    from .agent.runtime import RunResult
                    if not isinstance(result,RunResult):raise ChatPersistenceError()
                    outcome=replace(outcome,result=result)
            return outcome
        except BaseException:
            if admitted:
                with self._lock:self._unresolved.add(task_id)
            raise
        finally:
            with self._lock:
                if self._active.get(task_id) is token:self._active.pop(task_id)
