"""Explicit one-shot manual final action; no timer or positive admission change.

Only a fresh exact observer handoff can allocate assessment ownership. Normal
pre-allocation waits are repeatable; allocated work is never replayed. Canonical
model text is not proof: the existing settlement store checks local graph truth.
"""
from dataclasses import dataclass,fields
from threading import Lock,Event,get_ident
from .final_assessment import FinalAssessmentRunner,CATALOG_DIGEST
from ..db.final_assessment import FinalConfiguration,FinalAssessmentJournal
from ..db.final_settlement import FinalSettlementStore
from ..chat_transcripts import ChatTranscriptAdapter
from ..transcripts import TranscriptStore

class FinalizationUnavailable(RuntimeError):pass
class FinalizationUncertain(FinalizationUnavailable):pass

@dataclass(frozen=True)
class FinalizationPlan:
    configuration:object
    transcript:object
    start_authorize:object
    journal_authorize:object
    provider_resolver:object
    model_authorize:object
    read_authorize:object
    persist_authorize:object
    settlement_authorize:object
    clock:object
    def __post_init__(self):
        if type(self.configuration) is not FinalConfiguration or self.configuration.tool_catalog_digest!=CATALOG_DIGEST:
            raise ValueError('Exact restricted final configuration required')
        if type(self.transcript) is not ChatTranscriptAdapter or type(self.transcript.store) is not TranscriptStore:
            raise ValueError('Canonical final transcript required')
        if any(not callable(getattr(self,f.name)) for f in fields(self)[2:]):raise ValueError('Explicit final policies and clock required')

@dataclass(frozen=True)
class FinalizationResult:
    phase:str
    receipt:object=None

@dataclass(frozen=True,eq=False)
class FinalizationHandoff:
    owner:object
    observer:object
    assessment:object
    freeze:object
    lease:object

class OwnedFinalization:
    def __init__(self,observer,plan):
        from .expiry_observer import OwnedExpiryObserver
        if type(observer) is not OwnedExpiryObserver or type(plan) is not FinalizationPlan:
            raise ValueError('Actual observer and explicit final plan required')
        self.observer=observer;self.plan=plan;self._observer=observer;self._plan=plan
        self._pins=tuple(getattr(plan,f.name) for f in fields(plan));self._digest=plan.configuration.digest
        t=plan.transcript;self._transcript=(t.store,t.store.root,t.agent_id,t.recovery_guard,t.store.max_bytes)
        self._lock=Lock();self._cancel=Event();self._active=False;self._used=False;self._failed=False
        self._handoff=None;self.journal=None;self.runner=None;self.outcome=None;self.settlement=None;self.result=None;self._owned_journal=None;self._owned_runner=None;self._owned_settlement=None
    def _validate(self):
        if self.observer is not self._observer or self.plan is not self._plan or any(getattr(self.plan,f.name) is not value for f,value in zip(fields(self.plan),self._pins)) or self.plan.configuration.digest!=self._digest:
            raise FinalizationUnavailable('Final plan changed')
        if self.journal is not self._owned_journal or self.runner is not self._owned_runner or self.settlement is not self._owned_settlement:
            raise FinalizationUnavailable('Final resources changed')
        t=self.plan.transcript;s,root,agent,recovery,bound=self._transcript
        if t.store is not s or t.store.root!=root or t.agent_id!=agent or t.recovery_guard is not recovery or t.store.max_bytes!=bound:
            raise FinalizationUnavailable('Final transcript changed')
    def _preflight(self,receipt):
        self._validate()
        if self._cancel.is_set():raise FinalizationUnavailable('Final action cancelled')
        if self._pins[2](receipt) is not True:raise FinalizationUnavailable('Final action denied')
        self._validate()
        if self._cancel.is_set():raise FinalizationUnavailable('Final action cancelled')
    def cancel(self):
        with self._lock:
            if self.result is not None or self._failed:return False
            self._cancel.set();runner=self.runner
        if runner is not None:runner.cancel()
        return True
    def run_once(self):
        with self._lock:
            if self._active or self._used or self._failed or self._cancel.is_set():raise FinalizationUnavailable('Final action unavailable for replay')
            self._active=True;self._thread=get_ident()
        try:
            self._validate()
            handoff=self._observer.claim_finalization(self,self._plan)
            if type(handoff) is FinalizationResult:return handoff
            self._handoff=handoff;self._used=True
            if type(handoff) is not FinalizationHandoff or handoff.owner is not self or handoff.observer is not self._observer or self._observer._transfer is not handoff:
                raise FinalizationUnavailable('Exact final handoff required')
            self._validate()
            if self._cancel.is_set():raise FinalizationUnavailable('Final action cancelled')
            h,k,store,manager,handle,token,_,_=self._observer._pins
            p=self._plan
            def call(callback,*args,execution=False):
                self._validate()
                if execution and self._cancel.is_set():raise FinalizationUnavailable('Final action cancelled')
                value=callback(*args)
                self._validate()
                if execution and self._cancel.is_set():raise FinalizationUnavailable('Final action cancelled')
                return value
            self.journal=FinalAssessmentJournal(store.executor,manager=manager,assessment=handoff.assessment,
                freeze=handoff.freeze,handle=handle,token=token,lease=handoff.lease,configuration=p.configuration,
                transcript=p.transcript,lifecycle_guard=store.guard,authorize=lambda *a:call(p.journal_authorize,*a))
            self._owned_journal=self.journal
            self._validate()
            runner=FinalAssessmentRunner(self.journal,provider_resolver=lambda *a:call(p.provider_resolver,*a,execution=True),authorize=lambda *a:call(p.model_authorize,*a,execution=True),
                read_authorize=lambda *a:call(p.read_authorize,*a,execution=True),persist_authorize=lambda *a:call(p.persist_authorize,*a),clock=p.clock)
            with self._lock:
                self.runner=runner;self._owned_runner=runner
                if self._cancel.is_set():raise FinalizationUnavailable('Final action cancelled')
            self.outcome=runner.run()
            self._validate()
            if self._cancel.is_set():raise FinalizationUnavailable('Final action cancelled')
            self.settlement=FinalSettlementStore(self.journal,self.outcome.receipt,authorize=lambda *a:call(p.settlement_authorize,*a,execution=True))
            self._owned_settlement=self.settlement
            receipt=self.settlement.settle()
            # Settlement itself has confirmed all final authority and retired
            # ownership. No arbitrary callback may undo that confirmed result.
            self.result=FinalizationResult('settled',receipt)
            return self.result
        except BaseException:
            # Handoff may have allocated before an exception returned to us.
            handoff=getattr(self._observer,'_transfer',None)
            if type(handoff) is FinalizationHandoff and handoff.owner is self:
                self._handoff=handoff;self._used=True;self._failed=True
                try:self._observer._pins[3].quarantine_deadline_freeze(handoff.freeze)
                except BaseException:pass
                raise FinalizationUncertain('Final action unconfirmed; ownership retained, no replay') from None
            raise
        finally:
            with self._lock:self._active=False;self._thread=None
