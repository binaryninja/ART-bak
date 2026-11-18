"""Synchronous permission-gated chat with explicit durable transcript adapters.

Source: agent/chat.go creates/resumes a session, records human/assistant/tool turns,
captures normalized main requests, and closes the session. This narrower port
uses BoundedAgent; NEVER source ModeBypass/default shell/web/skills/MCP tools.
No filesystem, threads, listener, provider transport or deferred unlock; optional
working-context compaction requires explicit ChatCompaction. No
automatic retry is supplied. Streaming profiles use bounded whole-turn assembly. No task/goal completion is implied.

Adapter contract: begin must atomically reserve a running turn and persist its
user message, returning the full validated history including that user message.
Duplicate client IDs must reject changed text or return the existing turn without
re-execution. Reused turns must explicitly mark finalized only when their complete
result is durable; created=False alone does not confirm persistence. commit must atomically CAS turn id/revision against clear/deletion
and append the COMPLETE generated assistant/tool-result sequence plus reason and
usage, not a partially written tool pair. Return False only for stale generation.
current must check the turn generation before each admitted model/tool boundary;
clears/cancellation cannot preempt a callback already executing. An unresolved
prior running turn must block new admission; process-local locking
alone is not sufficient for multiple processes. Failed final persistence raises
ChatPersistenceError with the in-memory result: effects may already have happened,
so callers must reconcile, not automatically retry. Crash recovery is unported.
"""
from copy import deepcopy
from dataclasses import dataclass,field
from threading import RLock,Event
import re
from typing import Protocol
from .runtime import Action,Grant,BoundedAgent,Limits,RunResult,RunStopped
from ..sidequestion import Model
from .streaming import StreamingCompletion, StreamLimits
from .chat_compaction import ChatCompaction
import json


class ChatBusy(RuntimeError):pass
class ChatUnavailable(RuntimeError):pass
class ChatPersistenceError(RuntimeError):
    def __init__(self,result=None):
        super().__init__('Chat persistence unconfirmed; effects may have occurred; do not retry automatically')
        self.result=deepcopy(result)


@dataclass(frozen=True)
class TranscriptTurn:
    id:str
    revision:int
    run_id:int
    messages:list=field(repr=False)
    created:bool=True
    finalized:bool=False


class TranscriptAdapter(Protocol):
    def begin(self,session_id:str,client_id:str,user_message:dict)->TranscriptTurn: ...
    def current(self,session_id:str,turn:TranscriptTurn)->bool: ...
    def commit(self,session_id:str,turn:TranscriptTurn,result:RunResult)->bool: ...


@dataclass(frozen=True)
class ChatBinding:
    model:Model
    provider:object=field(repr=False)
    tool_events:object=field(default=None,repr=False,compare=False)


@dataclass(frozen=True)
class ChatCapture:
    session_id:str
    run_id:int
    version:int
    model:Model
    request:dict=field(repr=False)


@dataclass(frozen=True)
class ChatOutcome:
    turn:TranscriptTurn
    result:RunResult|None
    persisted:bool
    reused:bool=False
    stale:bool=False


class _Token:
    def __init__(self,external=None):
        self.event=Event();self.external=external
        self._external_cancelled()  # validate before admitting any durable turn
    def _external_cancelled(self):
        if self.external is None:return False
        value=getattr(self.external,'cancelled',None)
        if callable(value):value=value()
        if type(value) is not bool:raise ValueError('Cancellation token must expose boolean cancelled')
        return value
    @property
    def cancelled(self):return self.event.is_set() or self._external_cancelled()


class _Provider:
    def __init__(self,host,session,turn,model):
        self.host,self.session,self.turn,self.model=host,session,turn,model
        self.version=0
    def complete(self,context,request):
        context.check()
        self.host._check_turn(self.session,self.turn)
        binding=self.host._binding(self.model)
        context.check()
        self.host._check_turn(self.session,self.turn)
        if self.host.compaction is not None:
            def live():
                context.check()
                self.host._check_turn(self.session,self.turn)
                self.host._binding(self.model)
                self.host._check_turn(self.session,self.turn)
                context.check()
                return True
            outer=self
            class SummaryProvider:
                def complete(inner,ctx,summary_request):
                    live()
                    fresh=outer.host._binding(outer.model)
                    # Resolver callbacks may block or invalidate this turn.
                    # Fence after the final resolution before any transport.
                    ctx.check()
                    outer.host._check_turn(outer.session,outer.turn)
                    ctx.check()
                    provider=fresh.provider
                    if outer.model.streaming:
                        provider=StreamingCompletion(provider,check=live,
                            limits=StreamLimits(max_bytes=outer.host.limits.max_request_bytes),
                            usage_callback=ctx.charge_auxiliary_usage)
                        return provider.complete(ctx,summary_request)
                    result=provider.complete(ctx,summary_request)
                    ctx.charge_auxiliary_usage(result[2])
                    return result
            request=self.host.compaction.prepare(context,request,provider=SummaryProvider(),
                gate=self.host.gate,current=live,usage_accounted=True)
            live()
            # The outer runtime gate saw the raw request. Authorize the exact
            # transformed main request separately, never reuse its old grant.
            encoded=json.dumps(request,ensure_ascii=False,allow_nan=False,separators=(',',':'))
            if len(encoded.encode('utf-8'))>self.host.limits.max_request_bytes:raise RunStopped('request_budget')
            grant=self.host.gate(context,Action('model','complete',encoded))
            if not isinstance(grant,Grant) or not all(v is True for v in (grant.permission,grant.in_scope,grant.approved)):
                raise RunStopped('permission_denied')
            live()
        if self.host.capture is not None:
            self.version+=1
            self.host.capture(ChatCapture(self.session,self.turn.run_id,self.version,deepcopy(binding.model),deepcopy(request)))
        context.check()
        # Capture may persist/block; re-resolve after it so a profile edit during
        # capture cannot execute the previously cached provider.
        binding=self.host._binding(self.model)
        context.check()
        self.host._check_turn(self.session,self.turn)
        if self.model.streaming:
            def live():
                self.host._check_turn(self.session,self.turn)
                self.host._binding(self.model)
                self.host._check_turn(self.session,self.turn)
            return StreamingCompletion(binding.provider,check=live,
                limits=StreamLimits(max_bytes=self.host.limits.max_request_bytes)).complete(context,request)
        return binding.provider.complete(context,request)


class ChatAgent:
    def __init__(self,transcript:TranscriptAdapter,resolver,*,gate,tools=(),capture=None,limits=Limits(),clock=None,compaction=None,terminal_intercept=None):
        if any(not callable(getattr(transcript,name,None)) for name in ('begin','current','commit')):
            raise ValueError('Explicit transcript adapter required')
        if not callable(resolver) or not callable(gate):raise ValueError('Explicit resolver and permission gate required')
        if capture is not None and not callable(capture):raise ValueError('Capture must be callable')
        if compaction is not None and not isinstance(compaction,ChatCompaction):raise ValueError('Explicit compaction configuration required')
        self.compaction=compaction
        self.transcript,self.resolver,self.gate,self.tools=transcript,resolver,gate,tuple(tools)
        self.capture,self.limits,self.clock=capture,limits,clock
        self._lock=RLock();self._running={}
        self.terminal_intercept=terminal_intercept;self._terminal_intercept=terminal_intercept
        if terminal_intercept is not None:
            from ..intercept.durable import ConversationTerminalIntercept
            if type(terminal_intercept) is not ConversationTerminalIntercept:raise ValueError('Exact conversation interception required')
            terminal_intercept._bind_agent(self)

    def _binding(self,expected):
        try:binding=self.resolver(deepcopy(expected))
        except Exception:raise ChatUnavailable('Current model configuration unavailable') from None
        if (not isinstance(binding,ChatBinding) or not isinstance(binding.model,Model)
            or binding.model!=expected or not callable(getattr(binding.provider,'stream' if expected.streaming else 'complete',None))):
            raise ChatUnavailable('Current model configuration changed or unavailable')
        return binding

    def _check_turn(self,session,turn):
        try:current=self.transcript.current(session,deepcopy(turn))
        except Exception:raise RunStopped('transcript_unavailable') from None
        if current is not True:raise RunStopped('stale_transcript')

    def cancel(self,session_id):
        with self._lock:
            token=self._running.get(session_id)
            if token is None:return False
            token.event.set();return True

    def run(self,session_id,client_id,message,*,model:Model,system='',metadata=None,token=None):
        for value in (session_id,client_id):
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value):raise ValueError('Invalid chat identifier')
        if not isinstance(message,str) or not message.strip() or '\x00' in message:raise ValueError('Nonempty chat message required')
        if not isinstance(system,str):raise ValueError('System prompt must be text')
        message.encode('utf-8');system.encode('utf-8')
        if not isinstance(model,Model) or not model.identity:
            raise ValueError('Identified model configuration required')
        if self.compaction is not None and self.compaction.budget.context_window!=(model.window_tokens or 200000):
            raise ValueError('Compaction window must match identified model')
        user={'role':'user','content':[{'type':'text','text':message}]}
        if self.terminal_intercept is not self._terminal_intercept:raise ValueError('Interception binding changed')
        if self._terminal_intercept is not None:self._terminal_intercept._preflight(self,session_id,model)
        token=_Token(token)
        if token.cancelled:raise RunStopped('cancelled')
        with self._lock:
            if session_id in self._running:raise ChatBusy('Conversation already running')
            self._running[session_id]=token
        intercept_session=None
        try:
            binding=self._binding(model)  # fail before user persistence when config missing
            # Validate the same immutable tool registry before admitting a user turn.
            preflight=BoundedAgent(StreamingCompletion(binding.provider,check=lambda:None) if model.streaming else binding.provider,gate=self.gate,tools=self.tools,limits=self.limits,tool_events=binding.tool_events)
            if not isinstance(self.limits,Limits) or (self.clock is not None and not callable(self.clock)):
                raise ValueError('Invalid runtime configuration')
            context=dict(metadata or {})
            if token.cancelled:raise RunStopped('cancelled')
            try:turn=self.transcript.begin(session_id,client_id,deepcopy(user))
            except Exception:raise ChatPersistenceError() from None
            if (not isinstance(turn,TranscriptTurn) or not isinstance(turn.id,str) or not turn.id
                or type(turn.revision) is not int or turn.revision<0 or type(turn.run_id) is not int or turn.run_id<=0
                or type(turn.created) is not bool or type(turn.finalized) is not bool
                or (turn.created and turn.finalized)):raise ChatPersistenceError()
            if not turn.created:return ChatOutcome(deepcopy(turn),None,turn.finalized,reused=True)
            if not isinstance(turn.messages,list) or not turn.messages or turn.messages[-1]!=user:
                raise ChatPersistenceError()
            if self._terminal_intercept is not None:
                intercept_session=self._terminal_intercept._open(self,session_id,turn,token,model)
            def checked_gate(context,action):
                self._check_turn(session_id,turn)
                self._binding(model)
                grant=self.gate(context,action)
                self._check_turn(session_id,turn)
                self._binding(model)
                self._check_turn(session_id,turn)
                return grant
            from ..providers import Config, serialize
            try:serialize(Config('openai','history-validation'),{'messages':turn.messages})
            except Exception:raise ChatPersistenceError() from None
            options={'gate':checked_gate,'tools':tuple(preflight.tools.values()),'limits':self.limits,'tool_events':binding.tool_events}
            if intercept_session is not None:options['_terminal_dispatch']=intercept_session
            if self.clock is not None:options['clock']=self.clock
            runner=BoundedAgent(_Provider(self,session_id,turn,model),**options)
            request={'messages':deepcopy(turn.messages),'system':[system] if system else []}
            context.update(session_id=session_id,chat_turn_id=turn.id)
            result=runner.run(request,token=token,metadata=context)
            # Full history and all tool pairs travel in one atomic adapter call.
            try:persisted=self.transcript.commit(session_id,deepcopy(turn),deepcopy(result))
            except Exception:raise ChatPersistenceError(result) from None
            if type(persisted) is not bool:raise ChatPersistenceError(result)
            return ChatOutcome(deepcopy(turn),result,persisted,stale=not persisted)
        finally:
            if intercept_session is not None:intercept_session.close()
            with self._lock:self._running.pop(session_id,None)
