"""Explicit one-shot final assessment; not timeout admission or task settlement.

Uses the separate journal, canonical ChatAgent and one bounded local read tool.
No proof writes, intent creation, worker launch or generic supplied tool callback.
The model transport is explicitly provided and separately authorized. Deadlines
are cooperative: a blocked provider cannot be forcibly killed by this runner.
No automatic replay, pending adoption, restart recovery or terminal task writes.
"""
from dataclasses import dataclass,fields
from threading import Lock,Event,get_ident
import hashlib,json,math,time
from .chat import ChatAgent,ChatBinding
from .runtime import Tool,Grant,RuntimeContext
from ..db.final_assessment import FinalAssessmentJournal,AssessmentConflict,AssessmentUncertain
from ..db.exploration import ExplorationStore

_SCHEMA={'type':'object','properties':{},'additionalProperties':False}
CATALOG_DIGEST=hashlib.sha256(json.dumps({'list_goals':_SCHEMA},sort_keys=True,separators=(',',':')).encode()).hexdigest()
_MESSAGE='Assess the existing local goal states only. Do not create work or claim task completion.'

@dataclass(frozen=True)
class FinalAssessmentOutcome:
    receipt:object
    outcome:object

@dataclass(frozen=True)
class FinalProviderScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    model:object
    attempt_id:int
    bootstrap_identity:str


_INVOCATION_KEY=object()


class FinalProviderInvocation:
    """Original runner-issued final authority, never a receipt-only grant.

    current is callback-free for use inside provider lifetime guards. SQL and
    clock validation belong to validate_execution outside those guards.
    """
    def __init__(self,runner,key):
        if key is not _INVOCATION_KEY or type(runner) is not FinalAssessmentRunner:
            raise AssessmentConflict('Only the original final runner issues invocations')
        j=runner.journal;r=runner._receipt;c=j.configuration
        self._runner=runner;self._journal=j;self._receipt=r;self._clock=runner.clock;self._deadline=runner._deadline
        self._scope=FinalProviderScope(r.task_id,r.exploration_id,r.run_id,r.final_config,c.model,r.attempt_id,r.bootstrap_identity)
        self._original_scope=self._scope
        self._task_provider=runner._task_provider
        self._scope_fields=tuple(getattr(self._scope,f.name) for f in fields(self._scope))
        self._receipt_fields=tuple((type(getattr(r,f.name)),getattr(r,f.name)) for f in fields(r))
        self._resources=tuple(getattr(j,n) for n in ('executor','manager','assessment','freeze','handle','token','lease','configuration','transcript','guard','authorize'))
        self._config=c.digest;self._cancel=runner._cancel;self._refs=runner._refs
        t=j.transcript;self._transcript=(t.store,t.store.root,t.store.max_bytes,t.agent_id,t.recovery_guard)
        self._context=None;self._context_token=None;self._closed=False
    @property
    def scope(self):return self._scope
    @property
    def journal(self):return self._journal
    @property
    def receipt(self):return self._receipt
    @property
    def clock(self):return self._clock
    @property
    def deadline(self):return self._deadline
    def _pure(self):
        r=self._runner;j=self._journal;receipt=self._receipt
        if (r._invocation is not self or r._invocation_revoked or self._closed or not r._active
            or r._thread!=get_ident() or r._cancel is not self._cancel or self._cancel.is_set()
            or r.journal is not j or r._receipt is not receipt or j._receipt is not receipt or r.clock is not self._clock
            or r._deadline!=self._deadline or r._refs is not self._refs
            or self._scope is not self._original_scope or r.task_provider is not self._task_provider or r._task_provider is not self._task_provider):
            raise AssessmentConflict('Final provider invocation unavailable')
        if any(a is not b for a,b in zip((r.journal,r.resolver,r.authorize,r.read_authorize,r.persist_authorize,r.clock),self._refs)):
            raise AssessmentConflict('Final runner authority changed')
        if any(getattr(j,n) is not v for n,v in zip(('executor','manager','assessment','freeze','handle','token','lease','configuration','transcript','guard','authorize'),self._resources)) or j.configuration.digest!=self._config:
            raise AssessmentConflict('Final journal resources changed')
        if any(type(getattr(receipt,f.name)) is not typ or getattr(receipt,f.name)!=value for f,(typ,value) in zip(fields(receipt),self._receipt_fields)):
            raise AssessmentConflict('Final attempt changed')
        if any(getattr(self._scope,f.name) is not v for f,v in zip(fields(self._scope),self._scope_fields)):
            raise AssessmentConflict('Final provider scope changed')
        t=j.transcript;store,root,bound,agent,recovery=self._transcript
        if t.store is not store or t.store.root!=root or t.store.max_bytes!=bound or t.agent_id!=agent or t.recovery_guard is not recovery:
            raise AssessmentConflict('Final canonical transcript changed')
        return True
    def current(self,scope):
        if scope is not self._scope:raise AssessmentConflict('Original final provider scope required')
        return self._pure()
    def validate_execution(self):
        self._pure();self._runner._check();self._pure();return True
    def guard(self,scope):
        self.current(scope)
        return self._resources[9](self._scope.task_id,self._scope.exploration_id)
    def remaining_seconds(self):
        self.validate_execution();remaining=self._deadline-self._runner._now();self._pure()
        if not math.isfinite(remaining) or remaining<=0:raise AssessmentConflict('Final provider budget exhausted')
        return remaining
    def _admit_context(self,context,key):
        if key is not _INVOCATION_KEY:raise AssessmentConflict('Original final model gate required')
        self._pure()
        if type(context) is not RuntimeContext or context.clock is not self._clock or context._clock_identity is not self._clock:
            raise AssessmentConflict('Original final runtime context required')
        if self._context is not None and (self._context is not context or context.token is not self._context_token):raise AssessmentConflict('Final context replacement denied')
        self._context=context;self._context_token=context.token
        self.validate_context(context)
    def validate_context(self,context):
        self._pure()
        if (type(context) is not RuntimeContext or context is not self._context or context.token is not self._context_token
            or context.clock is not self._clock or context._clock_identity is not self._clock):
            raise AssessmentConflict('Original admitted final context required')
        for name,value in self._receipt.transcript_identity().items():
            if type(context.get(name)) is not type(value) or context.get(name)!=value:
                raise AssessmentConflict('Final runtime attempt identity changed')
        return True
    def _revoke(self,key):
        if key is not _INVOCATION_KEY:raise AssessmentConflict('Original final revocation required')
        self._closed=True


class FinalAssessmentRunner:
    def __init__(self,journal,*,provider_resolver=None,authorize,read_authorize,persist_authorize,clock=time.monotonic,task_provider=None):
        if task_provider is not None:
            from .final_task_provider import FinalTaskProviderAdapter
            if (type(task_provider) is not FinalTaskProviderAdapter or provider_resolver is not None
                or getattr(task_provider.bind,'__func__',None) is not FinalTaskProviderAdapter.bind):
                raise ValueError('Exclusive exact final task provider required')
        elif not callable(provider_resolver):raise ValueError('Explicit final provider required')
        if type(journal) is not FinalAssessmentJournal or any(not callable(x) for x in (authorize,read_authorize,persist_authorize,clock)):
            raise ValueError('Exact journal and independent final grants required')
        if journal.configuration.tool_catalog_digest!=CATALOG_DIGEST:raise ValueError('Exact restricted read catalog required')
        self.journal=journal;self.resolver=provider_resolver;self.authorize=authorize;self.read_authorize=read_authorize;self.persist_authorize=persist_authorize;self.clock=clock
        self.task_provider=self._task_provider=task_provider
        self._task_provider_bind=None if task_provider is None else task_provider.bind
        self._invocation=None;self._invocation_revoked=False
        self._refs=(journal,provider_resolver,authorize,read_authorize,persist_authorize,clock)
        self._config=journal.configuration.digest;self._lock=Lock();self._used=False;self._active=False;self._thread=None
        self._cancel=Event();self._deadline=None;self._last=None;self._receipt=None
    def cancel(self):
        with self._lock:
            if not self._active:return False
            self._cancel.set();return True
    def _pins(self,*,require_active=True):
        if self.task_provider is not self._task_provider or self._task_provider is not None and self._task_provider.bind!=self._task_provider_bind:raise AssessmentConflict('Final task adapter changed')
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
            self._invocation=FinalProviderInvocation(self,_INVOCATION_KEY)
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
                self._check()
                binding=(self.resolver(r,model) if self._task_provider is None else self._task_provider_bind(self._invocation,model))
                self._check()
                if type(binding) is not ChatBinding or binding.model!=config.model:raise AssessmentConflict('Final provider changed')
                return binding
            def gate(context,action):
                self._check();grant=self.authorize(r,context,action);self._check()
                if type(grant) is not Grant:return Grant()
                if action.kind=='model' and all(v is True for v in (grant.permission,grant.in_scope,grant.approved)):
                    self._invocation._admit_context(context,_INVOCATION_KEY)
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
            self._invocation_revoked=True;FinalProviderInvocation._revoke(self._invocation,_INVOCATION_KEY)
            with self._lock:self._active=False
            if not outcome.persisted or outcome.reused or outcome.stale:raise AssessmentUncertain('Canonical final result unconfirmed')
            self._pins(require_active=False)
            if journal.reconcile()!=r:raise AssessmentConflict('Final receipt changed')
            self._pins(require_active=False)
            final=journal.record_finalized(r,outcome.turn.id)
            self._pins(require_active=False)
            return FinalAssessmentOutcome(final,outcome)
        except BaseException:
            self._invocation_revoked=True
            if self._invocation is not None:FinalProviderInvocation._revoke(self._invocation,_INVOCATION_KEY)
            if started:journal._failed()
            raise AssessmentUncertain('Final assessment unavailable; inspect without replay') from None
        finally:
            self._invocation_revoked=True
            if self._invocation is not None:FinalProviderInvocation._revoke(self._invocation,_INVOCATION_KEY)
            with self._lock:self._active=False;self._thread=None
