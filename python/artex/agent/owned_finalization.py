"""Explicit one-shot manual final action; no timer or positive admission change.

Only a fresh exact observer handoff can allocate assessment ownership. Normal
pre-allocation waits are repeatable; allocated work is never replayed. Canonical
model text is not proof: the existing settlement store checks local graph truth.
"""
from dataclasses import dataclass,fields
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from threading import Lock,Event,get_ident
from .final_assessment import FinalAssessmentRunner,CATALOG_DIGEST
from ..db.final_assessment import FinalConfiguration,FinalAssessmentJournal
from ..db.final_settlement import FinalSettlementStore
from ..chat_transcripts import ChatTranscriptAdapter
from ..transcripts import TranscriptStore

def _retirement_state(value):
    """Exact scalar/container types, not Python numeric equality coercion."""
    kind=type(value)
    if kind in (str,int,float,bool,type(None)):return (kind,value)
    if kind is datetime:
        if type(value.tzinfo) not in (type(None),timezone,ZoneInfo):raise FinalizationUnavailable('Unsupported retirement clock type')
        return (kind,type(value.tzinfo),value.isoformat(),value.fold)
    if kind is tuple:return (kind,tuple(_retirement_state(v) for v in value))
    # Only code-owned dataclass receipts are supplied at these boundaries.
    try:members=fields(value)
    except TypeError:raise FinalizationUnavailable('Invalid retirement field type') from None
    return (kind,tuple((f.name,_retirement_state(getattr(value,f.name))) for f in members))

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
    task_provider:object=None
    def __post_init__(self):
        if type(self.configuration) is not FinalConfiguration or self.configuration.tool_catalog_digest!=CATALOG_DIGEST:
            raise ValueError('Exact restricted final configuration required')
        if type(self.transcript) is not ChatTranscriptAdapter or type(self.transcript.store) is not TranscriptStore:
            raise ValueError('Canonical final transcript required')
        if self.task_provider is not None:
            from .final_task_provider import FinalTaskProviderAdapter
            if type(self.task_provider) is not FinalTaskProviderAdapter or self.provider_resolver is not None:
                raise ValueError('Exclusive exact final task provider required')
        elif not callable(self.provider_resolver):raise ValueError('Explicit final provider required')
        if any(not callable(getattr(self,f.name)) for f in fields(self)[2:] if f.name not in ('provider_resolver','task_provider')):raise ValueError('Explicit final policies and clock required')

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
        self._retirement_binding=None;self._retirement=None;self._removal_pending=False;self._confirming=False
        self._tracking_lock=Lock();self._manual_owner=None;self._manual_owner_pin=None;self._manual_started=False
    def bind_runner_retirement(self,binder,actions):
        """Inert exact registration hook, never discovery/adoption after a run."""
        from .timed_lifecycle import PreparedTimedLifecycle,PreparedTimedActions
        from ..manager import TaskManager
        from .host import AgentHost
        if type(binder) is not PreparedTimedLifecycle or type(actions) is not PreparedTimedActions:
            raise FinalizationUnavailable('Original timed registration required')
        self._validate();host=self._observer._pins[0];handle=self._observer._pins[4]
        if (actions.finalizer is not self or actions.observer is not self._observer or actions.handle is not handle
            or binder.actions(handle) is not actions or host.runner._timed_lifecycle is not binder
            or self._manual_owner_pin is None or self._manual_owner_pin.host is not host
            or not AgentHost.owns_work(host,host.runner)):
            raise FinalizationUnavailable('Original owned registration required')
        with self._lock:
            if self._retirement_binding is not None or self._active or self._used or self._failed:
                raise FinalizationUnavailable('Retirement binding cannot replay')
            self._retirement_registration=(tuple(getattr(actions,f.name) for f in fields(actions)),self._observer._pins,binder.policy)
            self._retirement_binding=(binder,actions,host,host.runner,host.manager,handle,handle.token,
                host.runner.handles,TaskManager.retirement_snapshot,self._manual_owner_pin)
        return True
    def _retirement_pins(self):
        from .timed_lifecycle import PreparedTimedLifecycle
        from ..manager import TaskManager
        self._validate()
        b=self._retirement_binding
        if b is None:raise FinalizationUnavailable('No registered retirement continuation')
        binder,actions,host,runner,manager,handle,token,inventory,reader,owner=b
        action_fields,observer_pins,policy=self._retirement_registration
        if (any(getattr(actions,f.name) is not value for f,value in zip(fields(actions),action_fields))
            or self._observer._pins is not observer_pins or binder.policy is not policy
            or host.runner is not runner or host.manager is not manager or manager.runner is not runner
            or handle.token is not token or self._observer._pins[4] is not handle
            or self._observer._pins[3] is not manager or self._observer._pins[0] is not host
            or runner._timed_lifecycle is not binder or runner.handles!=inventory
            or actions.finalizer is not self or actions.observer is not self._observer or actions.handle is not handle
            or self._manual_owner is not owner or self._manual_owner_pin is not owner
            or TaskManager.retirement_snapshot is not reader):raise FinalizationUnavailable('Retirement resources changed')
        # Only inspect the code-owned registration ledger; no policy/SQL callbacks.
        with binder._lock:
            entry=binder._entries.get(handle)
            if entry is None or entry.failed or entry.actions is not actions or entry.token is not token:
                raise FinalizationUnavailable('Original retirement registration lost')
        return b
    def retirement_result(self,runner):
        """Callback-free consumed settlement proof; no task or inventory mutation."""
        from ..db.final_settlement import SettlementReceipt
        from ..manager import CancellationToken,DeadlineSettlement
        b=self._retirement_pins()
        binder,actions,host,original,manager,handle,token,inventory,reader,owner=b
        with self._lock:
            if runner is not original or self._active or self._failed or not self._used or not self._removal_pending or self._retirement is None:
                raise FinalizationUnavailable('Exact pending final retirement required')
            store,result,values,cap,capvalues,pins,final,finalvalues,handoff,jpins,ownership=self._retirement
            if (type(store) is not FinalSettlementStore or store is not self.settlement or store is not self._owned_settlement
                or type(result) is not SettlementReceipt or self.result is not self._retirement_public or self.result.phase!='settled'
                or self.result.receipt is not result or _retirement_state(result)!=values or store._result is not result
                or store._cap is not cap or type(cap) is not DeadlineSettlement or _retirement_state(cap)!=capvalues
                or cap.task_id!=str(result.task_id) or store._pins is not pins or store._evidence is not None
                or store._used is not True or store.receipt is not final or _retirement_state(final)!=finalvalues
                or store.journal is not self.journal or self.journal._receipt is not final or self._handoff is not handoff
                or self._observer._transfer is not handoff or handoff.owner is not self
                or self.journal._pins is not jpins
                or any(getattr(self.journal,name) is not value for name,value in zip(('executor','manager','assessment','freeze','handle','token','lease','configuration','transcript','guard','authorize'),jpins))
                or (_retirement_state(handoff.assessment),_retirement_state(handoff.freeze),_retirement_state(handoff.lease.receipt),_retirement_state(handoff.lease.clock))!=ownership
                or handoff.assessment is not self.journal.assessment or handoff.freeze is not self.journal.freeze
                or handoff.lease is not self.journal.lease or self.journal.configuration is not self.plan.configuration
                or self.journal.handle is not handle or self.journal.token is not token
                or self.journal.manager is not manager or self.journal.executor is not pins[3]
                or store.authorize is not pins[2] or self.journal.guard is not pins[4]
                or (result.attempt_id,result.task_id,result.exploration_id,result.run_id,result.final_revision,result.result_digest)
                   !=(final.attempt_id,final.task_id,final.exploration_id,final.run_id,final.revision,final.result_digest)
                or result.status not in ('done','timeout')):
                raise FinalizationUnavailable('Consumed final settlement evidence changed')
        if reader(manager,str(result.task_id))!=(result.status,result.completed_at.isoformat(),False,False) or not CancellationToken.cancelled.fget(token):
            raise FinalizationUnavailable('Final registry retirement unconfirmed')
        self._retirement_pins()
        return result
    def confirm_runner_removal(self):
        """Already-retired read-only continuation; no SQL/model/settlement retry."""
        b=self._retirement_pins();owner=b[-1]
        receipt=owner._enter(self,'confirm_runner_removal')
        entered=False
        try:
            with self._lock:
                if self._confirming:raise FinalizationUnavailable('Removal confirmation already active')
                self._confirming=True;entered=True
            result=self.retirement_result(b[3])
            values=b[7]()
            self._retirement_pins()
            if self.retirement_result(b[3]) is not result:raise FinalizationUnavailable('Final retirement changed')
            if type(values) not in (tuple,list) or len(values)>100000:raise FinalizationUnavailable('Invalid runner inventory')
            if any(v is b[5] for v in values):return False
            with self._lock:self._removal_pending=False
            return True
        finally:
            if entered:
                with self._lock:self._confirming=False
            owner._leave(receipt)

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
        from .manual_action_owner import ManualActionUnavailable
        with self._tracking_lock:
            owner=self._manual_owner_pin
            if self._manual_owner is not owner or self._observer._manual_owner_pin is not owner or self._observer._manual_owner is not owner:
                raise ManualActionUnavailable('Finalizer must share original observer tracking')
            self._manual_started=True;receipt=None if owner is None else owner._enter(self,'run_once')
        try:return self._run_once()
        finally:
            if owner is not None:owner._leave(receipt)
    def _run_once(self):
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
            runner=FinalAssessmentRunner(self.journal,provider_resolver=(lambda *a:call(p.provider_resolver,*a,execution=True)) if p.task_provider is None else None,task_provider=p.task_provider,authorize=lambda *a:call(p.model_authorize,*a,execution=True),
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
            if self._retirement_binding is not None:
                self._retirement_pins()
                store=self.settlement;self._retirement_public=self.result
                self._retirement=(store,receipt,_retirement_state(receipt),store._cap,_retirement_state(store._cap),store._pins,store.receipt,_retirement_state(store.receipt),handoff,self.journal._pins,
                    (_retirement_state(handoff.assessment),_retirement_state(handoff.freeze),_retirement_state(handoff.lease.receipt),_retirement_state(handoff.lease.clock)))
                self._removal_pending=True
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
