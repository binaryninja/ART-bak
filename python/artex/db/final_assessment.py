"""Separate final-assessment journal prerequisite; no runner or terminal writes.

The explicit installer adds a new table only to an explicitly selected database.
Original task/bootstrap schemas and wire values are untouched. One host-owned
clean-drained reservation may allocate one durable attempt. Pending/running or
uncertain attempts are never automatically taken over or replayed. Finalization
records verified canonical transcript evidence, NOT model-claimed goal truth.
An actual restricted model runner and fresh-truth terminal CAS are still absent;
positive runtime admission remains disabled.
Canonical evidence is rechecked around SQL/guard callbacks, but JSONL and SQL
are not an atomic transaction. A future terminal writer MUST reverify the same
canonical evidence and fresh graph truth; this receipt alone is not sufficient.
"""
from dataclasses import dataclass,asdict,field,replace
from copy import deepcopy
import hashlib,json,secrets
from ..agent.runtime import Limits
from ..sidequestion import Model
from ..chat_transcripts import ChatTranscriptAdapter
from ..transcripts import TranscriptStore
from ..manager import TaskManager,DeadlineAssessment,DeadlineFreeze,DeadlineLease
from .core import Query
from .schema import SchemaRefused,SchemaBootstrapError,IDENTITY_SQL,LOCK_KEY

class AssessmentConflict(RuntimeError):pass
class AssessmentUncertain(RuntimeError):pass

DDL="""CREATE TABLE public.artex_final_assessment (
 attempt_id BIGSERIAL PRIMARY KEY,
 task_id BIGINT NOT NULL REFERENCES public.tasks(id),
 exploration_id BIGINT NOT NULL REFERENCES public.explorations(id),
 run_id BIGINT NOT NULL,
 bootstrap_revision BIGINT NOT NULL,
 bootstrap_identity TEXT NOT NULL,
 final_config TEXT NOT NULL,
 first_run_at TIMESTAMPTZ NOT NULL,
 deadline_at TIMESTAMPTZ NOT NULL,
 timeout_seconds BIGINT NOT NULL CHECK(timeout_seconds>0),
 owner_token TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('reserved','running','finalized')),
 revision BIGINT NOT NULL DEFAULT 1,
 transcript_turn TEXT,
 result_digest TEXT,
 UNIQUE(task_id,run_id)
)"""
COLS='attempt_id,task_id,exploration_id,run_id,bootstrap_revision,bootstrap_identity,final_config,first_run_at,deadline_at,timeout_seconds,owner_token,phase,revision,transcript_turn,result_digest'

def install_final_assessment(executor,*,expected_database,expected_schema,allow_create=False):
    """Fresh-only additive migration; existing objects require manual inspection."""
    if type(expected_database) is not str or not expected_database or expected_schema!='public' or allow_create is not True:raise SchemaRefused('Explicit database/public additive migration approval required')
    try:
        with executor.transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
            name,version,readonly,strings,temp=tx.fetch_one(Query(IDENTITY_SQL))
            if name!=expected_database or version<120000 or readonly!='off' or strings!='on' or temp!=0:raise SchemaRefused('Final journal target incompatible')
            tx.execute_script('SET LOCAL search_path = public, pg_catalog')
            values=tx.fetch_one(Query("SELECT to_regclass('public.artex_final_assessment'),to_regclass('public.artex_final_assessment_attempt_id_seq'),to_regclass('public.tasks'),to_regclass('public.explorations'),to_regclass('public.artex_task_bootstrap')"))
            if any(v is not None for v in values[:2]) or any(v is None for v in values[2:]):raise SchemaRefused('Fresh final journal and existing task/bootstrap tables required')
            tx.execute_script(DDL)
    except (SchemaRefused,SchemaBootstrapError):raise
    except Exception:raise SchemaBootstrapError('Final journal installation unconfirmed; inspect safely') from None

@dataclass(frozen=True)
class FinalConfiguration:
    model:Model
    system:str=field(repr=False)
    policy_revision:str
    tool_catalog_digest:str
    limits:Limits
    def __post_init__(self):
        if type(self.model) is not Model or not self.model.identity or type(self.limits) is not Limits:raise ValueError('Identified model and finite limits required')
        if type(self.system) is not str or not self.system.strip() or len(self.system.encode())>1048576 or type(self.policy_revision) is not str or not 1<=len(self.policy_revision)<=256:raise ValueError('Explicit bounded final prompt/policy required')
        if type(self.tool_catalog_digest) is not str or len(self.tool_catalog_digest)!=64 or any(c not in '0123456789abcdef' for c in self.tool_catalog_digest):raise ValueError('Exact final tool catalog digest required')
    @property
    def digest(self):return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True)
class FinalReceipt:
    attempt_id:int
    task_id:int
    exploration_id:int
    run_id:int
    bootstrap_revision:int
    bootstrap_identity:str
    final_config:str
    first_run_at:object
    deadline_at:object
    timeout_seconds:int
    owner_token:str=field(repr=False)
    phase:str='reserved'
    revision:int=1
    transcript_turn:str|None=None
    result_digest:str|None=None
    @property
    def session_id(self):return f'exp{self.exploration_id}-final-r{self.run_id}-a{self.attempt_id}'
    def transcript_identity(self):
        return dict(role='final_assessment',task_id=self.task_id,exploration_id=self.exploration_id,run_id=self.run_id,
            revision=self.final_config,attempt_id=self.attempt_id,bootstrap_identity=self.bootstrap_identity,
            first_run_at=self.first_run_at.isoformat(),deadline_at=self.deadline_at.isoformat(),timeout_seconds=self.timeout_seconds)

class FinalAssessmentJournal:
    def __init__(self,executor,*,manager,assessment,freeze,handle,token,lease,configuration,transcript,lifecycle_guard,authorize):
        if type(configuration) is not FinalConfiguration or type(manager) is not TaskManager or type(assessment) is not DeadlineAssessment or type(freeze) is not DeadlineFreeze or type(lease) is not DeadlineLease:raise ValueError('Exact final reservation components required')
        if type(transcript) is not ChatTranscriptAdapter or type(transcript.store) is not TranscriptStore or not callable(authorize) or lifecycle_guard is not handle.coordinator.guard:raise ValueError('Canonical transcript and original guard/explicit journal policy required')
        if executor is not manager.repository.executor or executor is not handle.executor:raise ValueError('Exact owner database required')
        self.executor=executor;self.manager=manager;self.assessment=assessment;self.freeze=freeze;self.handle=handle;self.token=token;self.lease=lease
        self.configuration=configuration;self.transcript=transcript;self.guard=lifecycle_guard;self.authorize=authorize
        self._pins=(executor,manager,assessment,freeze,handle,token,lease,configuration,transcript,lifecycle_guard,authorize)
        self._transcript=(transcript.store,transcript.store.root,transcript.agent_id,transcript.recovery_guard,transcript.store.max_bytes)
        self._digest=configuration.digest;self._owner=secrets.token_hex(32);self._receipt=None;self._uncertain=False
        b=lease.receipt
        self._bootstrap=hashlib.sha256(json.dumps([b.task_id,b.exploration_id,b.run_id,b.request_id,b.config_digest,b.owner_token,b.revision],sort_keys=True).encode()).hexdigest()
        self._check()
    def _check(self,inspection=False):
        if any(a is not b for a,b in zip((self.executor,self.manager,self.assessment,self.freeze,self.handle,self.token,self.lease,self.configuration,self.transcript,self.guard,self.authorize),self._pins)) or self.configuration.digest!=self._digest:raise AssessmentConflict('Final journal binding changed')
        t=self.transcript
        if t.store is not self._transcript[0] or (t.store.root,t.agent_id,t.recovery_guard,t.store.max_bytes)!=self._transcript[1:]:raise AssessmentConflict('Final transcript changed')
        if not self.manager.validate_deadline_assessment(self.assessment,self.freeze,self.token,self.handle,self.lease,self._digest,inspection=inspection):raise AssessmentConflict('Final reservation unavailable')
        if self._uncertain and not inspection:raise AssessmentUncertain('Final journal needs reconciliation')
    def _allow(self,operation,inspection=False):
        self._check(inspection)
        if self.authorize(self.assessment,operation) is not True:raise AssessmentConflict('Final journal operation denied')
        self._check(inspection)
    def _parent(self,tx):
        b=self.lease.receipt;c=self.lease.clock
        rows=tx.fetch_all(Query('''SELECT first_run_at,deadline_at,timeout_seconds,deadline_at<=clock_timestamp() FROM tasks
WHERE id=$1 AND exploration_id=$2 AND status='running' AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE''',(b.task_id,b.exploration_id)))
        if rows!=[(c.first_run_at,c.deadline_at,c.timeout_seconds,True)]:raise AssessmentConflict('Exact expired task clock required')
        rows=tx.fetch_all(Query('''SELECT task_id FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2 AND run_id=$3
AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE''',
            (b.task_id,b.exploration_id,b.run_id,b.request_id,b.config_digest,b.owner_token,b.revision)))
        if rows!=[(b.task_id,)]:raise AssessmentConflict('Prepared generation changed')
    def _row(self,tx):
        b=self.lease.receipt
        rows=tx.fetch_all(Query(f'SELECT {COLS} FROM public.artex_final_assessment WHERE task_id=$1 AND run_id=$2 FOR UPDATE',(b.task_id,b.run_id)))
        return None if not rows else FinalReceipt(*rows[0]) if len(rows)==1 else (_ for _ in ()).throw(AssessmentConflict('Duplicate final attempt'))
    def _same(self,r):
        b=self.lease.receipt;c=self.lease.clock
        return (r.task_id,r.exploration_id,r.run_id,r.bootstrap_revision,r.bootstrap_identity,r.final_config,r.first_run_at,r.deadline_at,r.timeout_seconds,r.owner_token)==(b.task_id,b.exploration_id,b.run_id,b.revision,self._bootstrap,self._digest,c.first_run_at,c.deadline_at,c.timeout_seconds,self._owner)
    def _failed(self):
        self._uncertain=True
        try:self._pins[1].quarantine_deadline_freeze(self._pins[3])
        except BaseException:
            # Preserve the generic uncertain outcome even if registry cleanup
            # itself fails. Local uncertainty is sticky and never grants retry.
            pass
    def reserve(self):
        attempted=False;b=self.lease.receipt;c=self.lease.clock
        try:
            self._check()
            with self._pins[9](b.task_id,b.exploration_id):
                self._allow('reserve')
                with self.executor.transaction() as tx:
                    self._parent(tx);old=self._row(tx);self._allow('reserve')
                    if old is not None:
                        if self._receipt is None or old!=self._receipt or not self._same(old):raise AssessmentConflict('Existing final attempt cannot be adopted')
                        result=self._receipt
                    else:
                        attempted=True
                        row=tx.fetch_one(Query(f'''INSERT INTO public.artex_final_assessment(task_id,exploration_id,run_id,bootstrap_revision,bootstrap_identity,final_config,first_run_at,deadline_at,timeout_seconds,owner_token,phase)
    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'reserved') RETURNING {COLS}''',
                            (b.task_id,b.exploration_id,b.run_id,b.revision,self._bootstrap,self._digest,c.first_run_at,c.deadline_at,c.timeout_seconds,self._owner)))
                        result=FinalReceipt(*row)
                        if not self._same(result) or result.phase!='reserved' or type(result.attempt_id) is not int or result.attempt_id<=0 or result.revision!=1 or result.transcript_turn is not None or result.result_digest is not None:raise AssessmentConflict('Final allocation mismatch')
                    self._allow('reserve')
                self._allow('reserve')
            self._allow('reserve');self._receipt=result;return result
        except BaseException:
            if attempted:self._failed();raise AssessmentUncertain('Final allocation unconfirmed; inspect without replay') from None
            raise
    def start(self,receipt):return self._transition(receipt,'running',None)
    def record_finalized(self,receipt,turn_id):return self._transition(receipt,'finalized',turn_id)
    def _transition(self,receipt,phase,turn_id):
        attempted=False;b=self.lease.receipt
        try:
            self._check()
            with self._pins[9](b.task_id,b.exploration_id):
                self._allow(phase)
                if type(receipt) is not FinalReceipt or receipt is not self._receipt or not self._same(receipt):raise AssessmentConflict('Exact retained attempt required')
                expected='reserved' if phase=='running' else 'running'
                if receipt.phase!=expected:raise AssessmentConflict('Final attempt phase conflict')
                digest=None;evidence=None
                def verify_evidence():
                    if evidence is not None:
                        current=self.transcript.finalized_scoped_result(receipt.session_id,turn_id,receipt.transcript_identity())
                        if current!=evidence:raise AssessmentConflict('Canonical final evidence changed')
                        self._check()
                if phase=='finalized':
                    evidence=self.transcript.finalized_scoped_result(receipt.session_id,turn_id,receipt.transcript_identity())
                    if evidence is None:raise AssessmentConflict('Confirmed scoped final transcript required')
                    digest=evidence.result_digest
                with self.executor.transaction() as tx:
                    self._parent(tx)
                    if self._row(tx)!=receipt:raise AssessmentConflict('Final journal revision changed')
                    self._allow(phase);verify_evidence();attempted=True
                    rows=tx.fetch_all(Query(f'''UPDATE public.artex_final_assessment SET phase=$1,revision=revision+1,transcript_turn=$2,result_digest=$3
WHERE attempt_id=$4 AND task_id=$5 AND exploration_id=$6 AND run_id=$7 AND bootstrap_revision=$8 AND bootstrap_identity=$9
AND final_config=$10 AND first_run_at=$11 AND deadline_at=$12 AND timeout_seconds=$13 AND owner_token=$14 AND phase=$15 AND revision=$16 RETURNING {COLS}''',
                        (phase,turn_id,digest,receipt.attempt_id,receipt.task_id,receipt.exploration_id,receipt.run_id,receipt.bootstrap_revision,receipt.bootstrap_identity,receipt.final_config,receipt.first_run_at,receipt.deadline_at,receipt.timeout_seconds,receipt.owner_token,expected,receipt.revision)))
                    if len(rows)!=1:raise AssessmentConflict('Final attempt CAS failed')
                    result=FinalReceipt(*rows[0])
                    if result!=replace(receipt,phase=phase,revision=receipt.revision+1,transcript_turn=turn_id,result_digest=digest):raise AssessmentConflict('Final receipt mismatch')
                    self._allow(phase);verify_evidence()
                self._allow(phase);verify_evidence()
            self._allow(phase);verify_evidence();self._receipt=result;return result
        except BaseException:
            if attempted:self._failed();raise AssessmentUncertain('Final journal commit unconfirmed; no inference retry') from None
            raise
    def reconcile(self):
        """Read the same original owner only; never clear uncertainty or resume."""
        b=self.lease.receipt
        self._check(True)
        with self._pins[9](b.task_id,b.exploration_id):
            self._allow('inspect',True)
            with self.executor.transaction() as tx:
                self._parent(tx);receipt=self._row(tx)
                if receipt is not None and not self._same(receipt):raise AssessmentConflict('Foreign final attempt')
                self._allow('inspect',True)
            self._allow('inspect',True)
        self._allow('inspect',True)
        if receipt is not None and receipt.phase=='finalized':
            evidence=self.transcript.finalized_scoped_result(receipt.session_id,receipt.transcript_turn,receipt.transcript_identity())
            if evidence is None or evidence.result_digest!=receipt.result_digest:raise AssessmentConflict('Finalized evidence requires reconciliation')
            self._check(True)
        return receipt
