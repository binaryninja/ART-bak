"""Explicit one-shot final assessment; not timeout admission or task settlement.

Uses the separate journal, canonical ChatAgent and one bounded local read tool.
No proof writes, intent creation, worker launch or generic supplied tool callback.
The model transport is explicitly provided and separately authorized. Deadlines
are cooperative: a blocked provider cannot be forcibly killed by this runner.
No automatic replay, pending adoption, restart recovery or terminal task writes.
"""
from dataclasses import dataclass
from threading import Lock,Event,get_ident
import hashlib,json,math,time
from .chat import ChatAgent,ChatBinding
from .runtime import Tool,Grant
from ..db.final_assessment import FinalAssessmentJournal,AssessmentConflict,AssessmentUncertain
from ..db.exploration import ExplorationStore

_SCHEMA={'type':'object','properties':{},'additionalProperties':False}
CATALOG_DIGEST=hashlib.sha256(json.dumps({'list_goals':_SCHEMA},sort_keys=True,separators=(',',':')).encode()).hexdigest()
_MESSAGE='Assess the existing local goal states only. Do not create work or claim task completion.'

@dataclass(frozen=True)
class FinalAssessmentOutcome:
    receipt:object
    outcome:object

class FinalAssessmentRunner:
    def __init__(self,journal,*,provider_resolver,authorize,read_authorize,persist_authorize,clock=time.monotonic):
        if type(journal) is not FinalAssessmentJournal or any(not callable(x) for x in (provider_resolver,authorize,read_authorize,persist_authorize,clock)):
            raise ValueError('Exact journal and independent final grants required')
        if journal.configuration.tool_catalog_digest!=CATALOG_DIGEST:raise ValueError('Exact restricted read catalog required')
        self.journal=journal;self.resolver=provider_resolver;self.authorize=authorize;self.read_authorize=read_authorize;self.persist_authorize=persist_authorize;self.clock=clock
        self._refs=(journal,provider_resolver,authorize,read_authorize,persist_authorize,clock)
        self._config=journal.configuration.digest;self._lock=Lock();self._used=False;self._active=False;self._thread=None
        self._cancel=Event();self._deadline=None;self._last=None;self._receipt=None
    def cancel(self):
        with self._lock:
            if not self._active:return False
            self._cancel.set();return True
    def _pins(self,*,require_active=True):
        if any(a is not b for a,b in zip((self.journal,self.resolver,self.authorize,self.read_authorize,self.persist_authorize,self.clock),self._refs)) or self.journal.configuration.digest!=self._config:
            raise AssessmentConflict('Final runner binding changed')
        self.journal._check()  # same-module journal identity, never execution grant
        if require_active and (not self._active or self._thread!=get_ident()):raise AssessmentConflict('Final invocation unavailable')
    def _now(self):
        value=self.clock()
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or self._last is not None and value<self._last:raise AssessmentConflict('Final clock invalid')
        self._last=value;return value
    def _check(self,*,execution=True):
        self._pins()
        if self.journal.reconcile()!=self._receipt:raise AssessmentConflict('Final receipt changed')
        self._pins()
        if execution and (self._cancel.is_set() or self._now()>=self._deadline):raise AssessmentConflict('Final execution stopped')
    def run(self):
        with self._lock:
            if self._used:raise AssessmentConflict('Final runner cannot be replayed')
            self._used=True;self._active=True;self._thread=get_ident()
        journal=self._refs[0];started=False
        try:
            self._pins();self._deadline=self._now()+journal.configuration.limits.max_seconds
            # Existing reserved/running receipts are not runnable by a new runner.
            if journal.reconcile() is not None:raise AssessmentConflict('Pending attempt cannot be adopted')
            self._receipt=journal.reserve();self._pins()
            self._receipt=journal.start(self._receipt);started=True;self._check()
            owner=self;r=self._receipt;config=journal.configuration
            class Token:
                @property
                def cancelled(inner):
                    owner._pins()
                    return owner._cancel.is_set() or owner._now()>=owner._deadline
            class Transcript:
                def begin(inner,session,client,user):
                    owner._check()
                    result=journal.transcript.begin_scoped(session,client,user,r.transcript_identity())
                    owner._check()
                    if not result.created:raise AssessmentConflict('Final transcript cannot replay')
                    return result
                def current(inner,session,turn):
                    owner._check()
                    result=journal.transcript.current(session,turn);owner._check();return result
                def commit(inner,session,turn,result):
                    owner._check(execution=False)
                    if owner.persist_authorize(r,result.reason) is not True:raise AssessmentConflict('Final persistence denied')
                    owner._check(execution=False)
                    saved=journal.transcript.commit(session,turn,result)
                    owner._check(execution=False);return saved
            def resolver(model):
                self._check();binding=self.resolver(r,model);self._check()
                if type(binding) is not ChatBinding or binding.model!=config.model:raise AssessmentConflict('Final provider changed')
                return binding
            def gate(context,action):
                self._check();grant=self.authorize(r,context,action);self._check()
                if type(grant) is not Grant:return Grant()
                return grant
            def validate(args):
                if type(args) is not dict or args:raise ValueError('No goal read arguments accepted')
                return True
            def goals(context,args):
                self._check();context.check()
                if self.read_authorize(r) is not True:raise AssessmentConflict('Final graph read denied')
                self._check()
                with journal.guard(r.task_id,r.exploration_id):
                    self._pins()
                    store=ExplorationStore(journal.executor,r.exploration_id,read_only=True)
                    nodes=store.list_goals_bounded(max_rows=1000,max_total_bytes=262144)
                    value=[dict(id=n.id,state=n.state,payload=n.payload) for n in nodes]
                self._check()
                if self.read_authorize(r) is not True:raise AssessmentConflict('Final graph read denied')
                self._check();context.check();return json.dumps(value,ensure_ascii=False,allow_nan=False)
            tool=Tool('list_goals','Read bounded existing local goal states; does not prove completion.',_SCHEMA,goals,validate)
            agent=ChatAgent(Transcript(),resolver,gate=gate,tools=(tool,),limits=config.limits,clock=self.clock)
            outcome=agent.run(r.session_id,'final-assessment',_MESSAGE,model=config.model,system=config.system,
                metadata=r.transcript_identity(),token=Token())
            # Revoke execution callbacks immediately after the turn returns,
            # before any journal/policy callback can attempt another model call.
            with self._lock:self._active=False
            if not outcome.persisted or outcome.reused or outcome.stale:raise AssessmentUncertain('Canonical final result unconfirmed')
            self._pins(require_active=False)
            if journal.reconcile()!=r:raise AssessmentConflict('Final receipt changed')
            self._pins(require_active=False)
            final=journal.record_finalized(r,outcome.turn.id)
            self._pins(require_active=False)
            return FinalAssessmentOutcome(final,outcome)
        except BaseException:
            if started:journal._failed()
            raise AssessmentUncertain('Final assessment unavailable; inspect without replay') from None
        finally:
            with self._lock:self._active=False;self._thread=None
