"""Explicit side-question execution bridge, not an automatically running server.

admit() reserves one of at most four slots and persists idempotent admission;
execute(handle) runs synchronously on the caller. No threads, transports, tool
executors, clearing endpoints, restart sweeps, or archive orchestration are made.
The caller must execute every created handle, even after cancel(), to settle it.
One coordinator must be shared by all parents for a process-wide concurrency cap.

The resolver MUST load current persisted profile configuration (before any cached
provider), or the current direct configuration for profile_id=0. Return a binding
whose identity is the upstream sideModel config hash. Missing/changed config
fails closed before admission/execution and before every actual model call. A
freshly resolved provider is used at each boundary, including summary calls.
No credentials belong in the Model DTO.
Main captures must already be API-normalized. publish_capture is explicit and
must only be connected to main-agent capture, not auxiliary model calls.

Cancellation/deadline and persisted request lifecycle are checked at callback and
provider boundaries. No background polling or blocked-call preemption exists:
providers/caller must enforce transport cancellation/timeouts themselves. Failed
final writes raise SidePersistenceError, never a false persisted-success result.
Database CAS prevents cleared, terminal, or old-generation rows being resurrected.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import re
from threading import Event, RLock
import time
from typing import Callable
from . import (Answer, AnswerError, CancelledError, ContextInfo, Exchange as ReplayExchange,
               Memory as ReplayMemory, Model as ServiceModel, Parent as ServiceParent,
               Replay, SideQuestionService, Snapshot as ServiceSnapshot)
from ..db.sidequestions import (Exchange, Memory, Model, Parent, SideBusy, SideInvalid,
                               SideParentGone, SideQuestionStore, Snapshot)


class SideUnavailable(RuntimeError): pass
class SideCapacity(RuntimeError): pass
class SidePersistenceError(RuntimeError):
    def __init__(self,exchange):
        super().__init__('Side answer could not be persisted; completion is unconfirmed')
        self.exchange=deepcopy(exchange)


@dataclass(frozen=True)
class ProviderBinding:
    model: Model
    provider: object = field(repr=False)


@dataclass(frozen=True)
class Admission:
    exchange: Exchange
    created: bool
    handle: object | None = field(default=None,repr=False)


@dataclass(frozen=True)
class Outcome:
    exchange: Exchange
    persisted: bool
    stale: bool = False


@dataclass
class _Run:
    store: SideQuestionStore
    snapshot: Snapshot
    exchange: Exchange
    deadline: float
    external_cancelled: Callable
    agent_question: str
    cancel: Event = field(default_factory=Event)
    executing: bool = False
    dispatch_owner: object | None = None
    stale: bool = False
    expired: bool = False


def _service_snapshot(snapshot):
    return ServiceSnapshot(ServiceParent(**asdict(snapshot.parent)),snapshot.run_id,snapshot.version,
        snapshot.captured_at.isoformat(),ServiceModel(**asdict(snapshot.model)),deepcopy(snapshot.request))


def _replay_exchange(e):
    return ReplayExchange(e.question,e.answer,e.status,e.ordinal,e.snapshot_at.isoformat())


class _CheckedProvider:
    def __init__(self,resolve,check):self._resolve=resolve;self._check=check
    def complete(self,request):
        self._check()
        provider=self._resolve()
        self._check()
        # Return actual usage even if cancellation arrived while blocked: the
        # service collects it before its own post-call cancellation check.
        return provider.complete(request)
    def stream(self,request):
        self._check()
        provider=self._resolve()
        self._check()
        yield from provider.stream(request)


class SideQuestionCoordinator:
    def __init__(self,resolver,*,max_running=4,timeout=120,output_tokens=0,clock=time.monotonic):
        if not callable(resolver) or not callable(clock):raise SideInvalid('resolver and clock are required')
        if type(max_running) is not int or not 1<=max_running<=4:raise SideInvalid('concurrency must be between 1 and 4')
        if type(timeout) not in (float,int) or not 0<timeout<=120:raise SideInvalid('timeout must be between 0 and 120 seconds')
        if type(output_tokens) is not int or output_tokens!=0 and not 256<=output_tokens<=32768:raise SideInvalid('invalid output token budget')
        self._resolver=resolver;self._clock=clock;self._timeout=timeout;self._output_tokens=output_tokens
        self._maximum=max_running;self._runs={};self._lock=RLock()

    def _binding(self,model):
        if not isinstance(model,Model) or not model.identity:raise SideUnavailable('Main model context is unavailable; run the main agent first')
        try:binding=self._resolver(deepcopy(model))
        except Exception:raise SideUnavailable('Model configuration is unavailable or changed; refresh main context') from None
        if (not isinstance(binding,ProviderBinding) or not isinstance(binding.model,Model)
            or binding.provider is None or not callable(getattr(binding.provider,'complete',None))
            or model.streaming and not callable(getattr(binding.provider,'stream',None))):
            raise SideUnavailable('Model configuration is unavailable or changed; refresh main context')
        current=binding.model
        if (current.profile_id,current.identity,current.format,current.model,current.streaming)!=(model.profile_id,model.identity,model.format,model.model,model.streaming):
            raise SideUnavailable('Model configuration is unavailable or changed; refresh main context')
        return binding.provider

    def publish_capture(self,store,capture):
        """Persist an explicit main Capture snapshot, including run/version CAS."""
        if isinstance(capture,ServiceSnapshot):
            try:
                capture=Snapshot(Parent(**asdict(capture.parent)),capture.run_id,capture.version,
                    datetime.fromisoformat(capture.captured_at.replace('Z','+00:00')),
                    Model(**asdict(capture.model)),deepcopy(capture.request))
            except (ValueError,TypeError,AttributeError):raise SideInvalid('invalid main capture') from None
        return store.save_snapshot(capture)

    def admit(self,store,client_id,question,*,agent_question=None,cancelled=lambda:False):
        if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',client_id):
            raise SideInvalid('invalid client request id')
        if not isinstance(question,str):raise SideInvalid('question must be text')
        question=question.strip()
        if not question or len(question)>4000 or '\x00' in question:raise SideInvalid('question must be 1–4000 characters')
        try:question.encode('utf-8')
        except UnicodeError:raise SideInvalid('invalid question text') from None
        if agent_question is None:agent_question=question
        if not isinstance(agent_question,str) or not agent_question.strip() or '\x00' in agent_question:
            raise SideInvalid('invalid prepared question')
        try:agent_question.encode('utf-8')
        except UnicodeError:raise SideInvalid('invalid prepared question text') from None
        if not callable(cancelled):raise SideInvalid('cancellation callback must be callable')
        with self._lock:
            if cancelled():raise CancelledError('Side question cancelled')
            existing=store.existing(client_id)
            if existing is not None:
                if existing.question!=question:raise SideInvalid('request id cannot be reused for another question')
                return Admission(deepcopy(existing),False)
            snapshot=store.snapshot()
            if snapshot is None:raise SideUnavailable('No main context snapshot; run the main agent first')
            if not isinstance(snapshot,Snapshot) or snapshot.parent!=store.parent or snapshot.run_id<=0 or snapshot.version<=0:
                raise SideUnavailable('Invalid main context snapshot; run the main agent first')
            self._binding(snapshot.model)
            if any(run.store.key==store.key for run in self._runs.values()):raise SideBusy('another side question is already running')
            if len(self._runs)>=self._maximum:raise SideCapacity('Side question concurrency limit reached')
            if cancelled():raise CancelledError('Side question cancelled')
            exchange,created=store.start(snapshot,client_id,question)
            if not created:return Admission(deepcopy(exchange),False)
            handle=object()
            self._runs[handle]=_Run(store,deepcopy(snapshot),deepcopy(exchange),self._clock()+self._timeout,cancelled,agent_question)
            return Admission(deepcopy(exchange),True,handle)

    def cancel(self,handle):
        """Request cancellation; execute() must drain/settle before claiming it stopped."""
        with self._lock:
            run=self._runs.get(handle)
            if run is None:return False
            run.cancel.set();return True

    def claim_dispatch(self,admission,owner):
        """Atomically bind a registered, unstarted handle to exactly one dispatcher."""
        if not isinstance(admission,Admission) or admission.created is not True or owner is None:
            raise SideInvalid('invalid dispatch admission')
        with self._lock:
            run=self._runs.get(admission.handle)
            if (run is None or run.executing or admission.exchange.id!=run.exchange.id
                or admission.exchange.session_key!=run.exchange.session_key
                or admission.exchange.generation!=run.exchange.generation
                or run.dispatch_owner is not None and run.dispatch_owner is not owner):
                raise SideInvalid('admission is foreign, invalid, or already owned')
            run.dispatch_owner=owner

    def abort_unstarted(self,handle,*,dispatch_owner=None):
        """Durably fail a rejected dispatch; retain ownership if persistence fails.

        Never abort an executing handle. CAS failure is only settled when a read
        confirms the row is gone, terminal, or from a different generation.
        """
        with self._lock:
            run=self._runs.get(handle)
            if run is None or run.executing or run.dispatch_owner is not dispatch_owner:
                raise SideInvalid('admission is absent, executing, or owned by a dispatcher')
            run.executing=True
        e=deepcopy(run.exchange);settled=False
        e.status='failed';e.error='Side request was not dispatched'
        try:
            for attempt in range(3):
                try:
                    e.sequence+=1
                    if run.store.update(e):
                        settled=True
                        return Outcome(deepcopy(e),True)
                    current=run.store.request(e.id)
                    if current is None or current.generation!=e.generation or current.status!='running':
                        settled=True
                        return Outcome(deepcopy(e),False,True)
                    e.sequence=max(e.sequence,current.sequence)
                except Exception:
                    pass
            raise SidePersistenceError(e) from None
        finally:
            with self._lock:
                if settled:self._runs.pop(handle,None)
                else:
                    run.executing=False
                    # Retries must advance beyond an ambiguous committed write.
                    run.exchange.sequence=e.sequence

    def _cancelled(self,run):
        if run.cancel.is_set() or run.external_cancelled():return True
        if self._clock()>=run.deadline:
            run.expired=True;return True
        current=run.store.request(run.exchange.id)
        if current is None or current.status!='running' or current.generation!=run.exchange.generation:
            run.stale=True;return True
        return False

    def execute(self,handle,*,dispatch_owner=None):
        with self._lock:
            run=self._runs.get(handle)
            if run is None or run.executing or run.dispatch_owner is not dispatch_owner:
                raise SideInvalid('unknown, executing, or differently owned admission')
            run.executing=True
        e=run.exchange;answer=Answer();info=ContextInfo();error=None;persisted=False
        def check():
            if self._cancelled(run):raise CancelledError('Side question stopped')
        def persist():
            nonlocal persisted
            e.sequence+=1
            persisted=run.store.update(e)
            if not persisted:run.stale=True;run.cancel.set()
            return persisted
        def update(partial,context):
            nonlocal answer,info
            answer=deepcopy(partial);info=deepcopy(context)
            e.answer=answer.text;e.usage=deepcopy(answer.usage);e.context=asdict(info)
            check()
            if not persist():raise CancelledError('Side request is no longer current')
        try:
            try:
                check()
                self._binding(run.snapshot.model)
                check()
                memory=run.store.memory(e)
                def load(after):
                    check()
                    return [_replay_exchange(row) for row in run.store.replay_page(e,after)]
                def save(memory):
                    check();run.store.save_memory(e,Memory(**asdict(memory)))
                replay=Replay(ReplayMemory(**asdict(memory)),load,save)
                service=SideQuestionService(_CheckedProvider(lambda:self._binding(run.snapshot.model),check))
                answer,info=service.respond(_service_snapshot(run.snapshot),run.agent_question,replay,
                    output_tokens=self._output_tokens,update=update,cancelled=lambda:self._cancelled(run))
                check()
            except Exception as exc:
                error=exc
                if isinstance(exc,AnswerError):
                    answer=deepcopy(exc.answer)
                    if exc.info is not None:info=deepcopy(exc.info)
            e.answer=answer.text;e.usage=deepcopy(answer.usage);e.context=asdict(info)
            # Do not let diagnostic/provider exception strings leak credentials.
            e.status='completed';e.error=''
            if run.expired:e.status='failed';e.error='Side answer exceeded its deadline'
            elif run.cancel.is_set() or run.stale or isinstance(error,CancelledError):
                e.status='cancelled';e.error='Answer stopped'
            elif error is not None:
                # A service wraps cooperative cancellation as AnswerError.
                cause=error;seen=set();was_cancelled=False
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause));was_cancelled|=isinstance(cause,CancelledError)
                    cause=cause.__cause__
                e.status='cancelled' if was_cancelled else 'failed'
                e.error='Answer stopped' if was_cancelled else 'Side answer failed'
            if run.stale:return Outcome(deepcopy(e),False,True)
            for attempt in range(3):
                try:
                    persist()
                    return Outcome(deepcopy(e),persisted,run.stale)
                except Exception:
                    if attempt==2:raise SidePersistenceError(e) from None
        finally:
            with self._lock:self._runs.pop(handle,None)
