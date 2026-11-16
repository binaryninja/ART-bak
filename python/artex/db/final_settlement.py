"""Explicit local final settlement; no runtime admission, inference or retries.

Narrower than source: inherited/goalless graphs and met goals without local
confirmed fact/finding proof are refused. Existing terminal tasks are preserved.
JSONL and SQL are not atomic; repeated canonical checks do not imply otherwise.
All graph writers must participate in the original host lifetime barrier and
parent-row lock. Mixing unfenced generic graph writers is unsupported.
"""
from dataclasses import dataclass,asdict
from datetime import datetime
import hashlib,json
from .core import Query
from .schema import SchemaRefused,SchemaBootstrapError,IDENTITY_SQL,LOCK_KEY
from .final_assessment import FinalAssessmentJournal,FinalReceipt,COLS,AssessmentConflict

class SettlementConflict(RuntimeError):pass
class SettlementUncertain(RuntimeError):pass

DDL="""CREATE TABLE public.artex_final_settlement (
 attempt_id BIGINT PRIMARY KEY REFERENCES public.artex_final_assessment(attempt_id),
 task_id BIGINT NOT NULL REFERENCES public.tasks(id),
 exploration_id BIGINT NOT NULL REFERENCES public.explorations(id),
 run_id BIGINT NOT NULL,
 final_revision BIGINT NOT NULL,
 final_identity TEXT NOT NULL,
 result_digest TEXT NOT NULL,
 graph_digest TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('done','timeout')),
 completed_at TIMESTAMPTZ NOT NULL,
 UNIQUE(task_id,run_id)
)"""

def install_final_settlement(executor,*,expected_database,expected_schema,allow_create=False):
    if type(expected_database) is not str or not expected_database or expected_schema!='public' or allow_create is not True:
        raise SchemaRefused('Explicit named database/public fresh installation required')
    try:
        with executor.transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
            name,version,readonly,strings,temp=tx.fetch_one(Query(IDENTITY_SQL))
            if name!=expected_database or version<120000 or readonly!='off' or strings!='on' or temp!=0:raise SchemaRefused('Settlement database incompatible')
            tx.execute_script('SET LOCAL search_path = public, pg_catalog')
            values=tx.fetch_one(Query("SELECT to_regclass('public.artex_final_settlement'),to_regclass('public.artex_final_assessment'),to_regclass('public.tasks'),to_regclass('public.explorations')"))
            if values[0] is not None or any(v is None for v in values[1:]):raise SchemaRefused('Fresh settlement table and existing assessment schema required')
            tx.execute_script(DDL)
    except (SchemaRefused,SchemaBootstrapError):raise
    except Exception:raise SchemaBootstrapError('Settlement installation unconfirmed') from None

@dataclass(frozen=True)
class SettlementReceipt:
    attempt_id:int
    task_id:int
    exploration_id:int
    run_id:int
    final_revision:int
    final_identity:str
    result_digest:str
    graph_digest:str
    status:str
    completed_at:datetime

FIELDS='attempt_id,task_id,exploration_id,run_id,final_revision,final_identity,result_digest,graph_digest,status,completed_at'

def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=lambda x:x.isoformat(),allow_nan=False).encode()).hexdigest()

class FinalSettlementStore:
    def __init__(self,journal,receipt,*,authorize):
        if type(journal) is not FinalAssessmentJournal or type(receipt) is not FinalReceipt or receipt.phase!='finalized' or not callable(authorize):raise ValueError('Exact finalized journal and explicit settlement policy required')
        self.journal=journal;self.receipt=receipt;self.authorize=authorize
        self._pins=(journal,receipt,authorize,journal.executor,journal.guard,journal.manager)
        self._identity=_digest(asdict(receipt));self._evidence=None;self._result=None;self._cap=None;self._used=False
    def _check(self):
        j,r,a,e,g,m=self._pins
        if self.journal is not j or self.receipt is not r or self.authorize is not a or j.executor is not e or j.guard is not g or j.manager is not m or _digest(asdict(r))!=self._identity:raise SettlementConflict('Settlement binding changed')
        j._check()
        if self._cap is not None and not m.validate_deadline_settlement(self._cap,self):raise SettlementConflict('Settlement ownership changed')
    def _allow(self,operation,status=None):
        self._check()
        if self._pins[2](self.receipt,operation,status) is not True:raise SettlementConflict('Settlement operation denied')
        self._check()
    def _canonical(self):
        r=self.receipt
        evidence=self.journal.transcript.finalized_scoped_result(r.session_id,r.transcript_turn,r.transcript_identity())
        if evidence is None or evidence.result_digest!=r.result_digest:raise SettlementConflict('Canonical final evidence unavailable')
        self._check();return evidence
    def validate_reservation(self):
        self._allow('reserve')
        if self.journal.reconcile()!=self.receipt:raise SettlementConflict('Final journal revision changed')
        self._canonical();self._allow('reserve')
        if self.journal.handle.wait(0) is not True or self.journal.handle.unresolved is not False:raise SettlementConflict('Final owner not cleanly drained')
        self._check();return True
    def _journal_row(self,tx):
        r=self.receipt
        rows=tx.fetch_all(Query(f'SELECT {COLS} FROM public.artex_final_assessment WHERE attempt_id=$1 FOR SHARE',(r.attempt_id,)))
        if rows!=[tuple(asdict(r).values())]:raise SettlementConflict('Final receipt changed')
    def _persisted(self,tx,result):
        """Verify the complete committed terminal outcome, never infer from INSERT."""
        r=self.receipt;b=self.journal.lease.receipt
        rows=tx.fetch_all(Query('SELECT status,completed_at,first_run_at,deadline_at,timeout_seconds FROM tasks WHERE id=$1 AND exploration_id=$2 AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE',(r.task_id,r.exploration_id)))
        if rows!=[(result.status,result.completed_at,r.first_run_at,r.deadline_at,r.timeout_seconds)]:raise SettlementConflict('Persisted terminal task changed')
        rows=tx.fetch_all(Query(f'SELECT {FIELDS} FROM public.artex_final_settlement WHERE attempt_id=$1 FOR SHARE',(r.attempt_id,)))
        if rows!=[tuple(asdict(result).values())]:raise SettlementConflict('Persisted settlement receipt changed')
        rows=tx.fetch_all(Query("SELECT task_id FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2 AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE",
            (b.task_id,b.exploration_id,b.run_id,b.request_id,b.config_digest,b.owner_token,b.revision)))
        if rows!=[(b.task_id,)]:raise SettlementConflict('Prepared generation changed')
        self._journal_row(tx)
    def _truth(self,tx):
        r=self.receipt
        if tx.fetch_all(Query('SELECT source_task_id FROM task_relations WHERE task_id=$1 LIMIT 1 FOR SHARE',(r.task_id,))):raise SettlementConflict('Inherited final settlement unsupported')
        nodes=tx.fetch_all(Query("SELECT id,kind,state FROM exploration_nodes WHERE exploration_id=$1 AND kind IN ('goal','fact','finding') ORDER BY id LIMIT 10001 FOR SHARE",(r.exploration_id,)))
        if len(nodes)>10000:raise SettlementConflict('Final graph exceeds bound')
        goals={};proofs=set();seen=set()
        for id,kind,state in nodes:
            if type(id) is not int or id<=0 or id in seen:raise SettlementConflict('Invalid graph identity')
            seen.add(id)
            if kind=='goal':
                if state not in ('open','met','abandoned'):raise SettlementConflict('Unknown goal state')
                goals[id]=state
            elif kind in ('fact','finding'):
                if state=='confirmed':proofs.add(id)
                elif state not in ('dismissed','origin'):raise SettlementConflict('Unknown evidence state')
            else:raise SettlementConflict('Unknown graph kind')
        if not goals:raise SettlementConflict('Goalless final settlement unsupported')
        edges=tx.fetch_all(Query("SELECT src_id,dst_id FROM exploration_edges WHERE exploration_id=$1 AND rel='proves' ORDER BY src_id,dst_id LIMIT 20001 FOR SHARE",(r.exploration_id,)))
        if len(edges)>20000 or any(type(a) is not int or type(b) is not int for a,b in edges):raise SettlementConflict('Invalid or excessive proof graph')
        proven={b for a,b in edges if a in proofs and b in goals}
        if any(state=='met' and id not in proven for id,state in goals.items()):raise SettlementConflict('Met goal lacks confirmed local proof')
        status='done' if all(state=='met' for state in goals.values()) else 'timeout'
        return status,_digest([nodes,edges])
    def settle(self):
        if self._used:raise SettlementConflict('Settlement cannot retry; inspect durable receipt')
        self._used=True;attempted=False;j=self._pins[0];r=self.receipt
        try:
            self._cap=j.manager.reserve_deadline_settlement(self)
            self._allow('read');evidence=self._canonical()
            with self._pins[4](r.task_id,r.exploration_id):
                self._allow('read')
                with self._pins[3].transaction() as tx:
                    # Parent-before-journal ordering matches final assessment.
                    j._parent(tx);self._journal_row(tx)
                    if tx.fetch_all(Query('SELECT attempt_id FROM public.artex_final_settlement WHERE attempt_id=$1 OR (task_id=$2 AND run_id=$3) FOR UPDATE',(r.attempt_id,r.task_id,r.run_id))):raise SettlementConflict('Settlement already exists; no adoption')
                    status,graph_digest=self._truth(tx)
                    self._allow('write',status)
                    if self._canonical()!=evidence:raise SettlementConflict('Final evidence changed')
                    if self._truth(tx)!=(status,graph_digest):raise SettlementConflict('Final graph changed')
                    attempted=True
                    rows=tx.fetch_all(Query("""UPDATE tasks SET status=$1,completed_at=COALESCE(completed_at,now())
WHERE id=$2 AND exploration_id=$3 AND status='running' AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND first_run_at=$4 AND deadline_at=$5 AND timeout_seconds=$6
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$2) RETURNING completed_at""",
                        (status,r.task_id,r.exploration_id,r.first_run_at,r.deadline_at,r.timeout_seconds)))
                    if len(rows)!=1 or len(rows[0])!=1 or not isinstance(rows[0][0],datetime) or rows[0][0].tzinfo is None:raise SettlementConflict('Terminal CAS failed')
                    result=SettlementReceipt(r.attempt_id,r.task_id,r.exploration_id,r.run_id,r.revision,self._identity,r.result_digest,graph_digest,status,rows[0][0])
                    written=tx.fetch_all(Query(f'INSERT INTO public.artex_final_settlement({FIELDS}) VALUES('+','.join('$'+str(n) for n in range(1,11))+f') RETURNING {FIELDS}',tuple(asdict(result).values())))
                    if written!=[tuple(asdict(result).values())]:raise SettlementConflict('Settlement receipt write mismatch')
                    self._allow('write',status);self._persisted(tx,result)
                    if self._canonical()!=evidence:raise SettlementConflict('Final evidence changed')
                    if self._truth(tx)!=(status,graph_digest):raise SettlementConflict('Final graph changed')
                self._allow('write',status)
                if self._canonical()!=evidence:raise SettlementConflict('Final evidence changed')
                with self._pins[3].transaction() as verify:
                    self._persisted(verify,result)
                    if self._truth(verify)!=(status,graph_digest):raise SettlementConflict('Final graph changed')
            self._allow('write',status)
            if self._canonical()!=evidence:raise SettlementConflict('Final evidence changed')
            with self._pins[3].transaction() as verify:
                self._persisted(verify,result)
                if self._truth(verify)!=(status,graph_digest):raise SettlementConflict('Final graph changed')
            self._allow('write',status)
            if self._canonical()!=evidence:raise SettlementConflict('Final evidence changed')
            self._persisted(self._pins[3],result)
            if self._truth(self._pins[3])!=(status,graph_digest):raise SettlementConflict('Final graph changed')
            self._check()
            self._result=result;self._evidence=object()
            return j.manager.confirm_deadline_settlement(self._cap,self,self._evidence)
        except BaseException:
            # Even a prewrite reserved attempt is deliberately non-retryable;
            # the original owner remains held for explicit inspection.
            if self._cap is not None:j._failed()
            if attempted:raise SettlementUncertain('Terminal settlement unconfirmed; do not retry inference or writes') from None
            raise SettlementConflict('Terminal settlement refused; ownership retained') from None
    def consume_evidence(self,cap,evidence):
        self._check()
        if cap is not self._cap or evidence is not self._evidence or evidence is None or self._result is None:raise SettlementConflict('Exact committed settlement evidence required')
        result=self._result;self._evidence=None;return result
