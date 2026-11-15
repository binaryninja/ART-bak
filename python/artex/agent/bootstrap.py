"""Explicit initialization journal and bounded GoalPlanner coordinator.

Python-only durable ownership extension, NOT an upstream schema migration.
TaskStore.create already creates the exploration and origin. This module never
creates another graph, starts an engine, changes task status, or infers approval.
Prepared means initialization verified, NOT runnable/complete. Existing pending
owners are never stolen, even after restart. SQL and transcript files are not one
transaction: any interruption after claim remains unresolved and needs operator
reconciliation. No raw-goal fallback, activity projection or scope extraction.
GoalPlanner.validate_input must attest scope prerequisites; optional reviewed
ConstraintTools remain its explicit dependency. No automatic factory wiring.
"""
from dataclasses import dataclass,field,asdict
import hashlib,json,secrets
from threading import get_ident
from contextlib import contextmanager
from .goals import GoalPlanner,GoalScope
from ..db.core import Query,int64
from ..db.schema import IDENTITY_SQL,LOCK_KEY,SchemaRefused,SchemaBootstrapError

class BootstrapUnavailable(RuntimeError):pass
class BootstrapConflict(BootstrapUnavailable):pass
class BootstrapUnresolved(BootstrapUnavailable):pass

DDL="""CREATE TABLE public.artex_task_bootstrap (
 task_id BIGINT PRIMARY KEY REFERENCES public.tasks(id),
 exploration_id BIGINT NOT NULL REFERENCES public.explorations(id),
 run_id BIGSERIAL UNIQUE NOT NULL,
 request_id TEXT NOT NULL,
 config_digest TEXT NOT NULL,
 owner_token TEXT NOT NULL,
 revision BIGINT NOT NULL DEFAULT 1,
 phase TEXT NOT NULL CHECK (phase IN ('running','prepared','unresolved')),
 goal_ids BIGINT[] NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

def install_journal(executor,*,expected_database,expected_schema,allow_create=False):
    """Fresh-only explicit installer. Existing objects are rejected, not trusted.

    No IF NOT EXISTS: even a nominally compatible existing table requires separate
    operator inspection. Uses dedicated SchemaExecutor, never DML execute for DDL.
    """
    if not expected_database or expected_schema!='public' or allow_create is not True:raise SchemaRefused('Explicit database/public schema creation authority required')
    try:
        with executor.transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
            name,version,readonly,strings,temp=tx.fetch_one(Query(IDENTITY_SQL))
            if name!=expected_database or version<120000 or readonly!='off' or strings!='on' or temp!=0:raise SchemaRefused('Bootstrap journal target incompatible')
            tx.execute_script('SET LOCAL search_path = public, pg_catalog')
            row=tx.fetch_one(Query("SELECT to_regclass('public.artex_task_bootstrap'), to_regclass('public.artex_task_bootstrap_run_id_seq'), to_regclass('public.tasks'), to_regclass('public.explorations')"))
            if row[0] is not None or row[1] is not None or row[2] is None or row[3] is None:raise SchemaRefused('Fresh journal and existing source task tables required')
            tx.execute_script(DDL)
    except (SchemaRefused,SchemaBootstrapError):raise
    except Exception:raise SchemaBootstrapError('Journal installation failed; inspect database safely') from None

@dataclass(frozen=True)
class Receipt:
    task_id:int
    exploration_id:int
    run_id:int
    request_id:str
    config_digest:str
    owner_token:str=field(repr=False)
    revision:int=1
    phase:str='running'
    goal_ids:tuple=()

COLS='task_id,exploration_id,run_id,request_id,config_digest,owner_token,revision,phase,goal_ids'
def _receipt(row):
    values=list(row);values[-1]=tuple(values[-1]);return Receipt(*values)

def _positive(value):
    if int64(value,'identity')<=0:raise ValueError('Positive identity required')
    return value

class BootstrapJournal:
    def __init__(self,executor):
        if not callable(getattr(executor,'transaction',None)):raise ValueError('Transactional executor required')
        self.executor=executor;self._executor=executor
    def _check(self):
        if self.executor is not self._executor:raise BootstrapConflict('Journal executor changed')
    def _task(self,tx,task_id,exploration_id,admission=None):
        status='created'
        if admission is not None:
            if type(admission) is not _InstalledAdmission:raise BootstrapConflict('Installed generation capability required')
            admission.check(self.executor,task_id,exploration_id)
            status='running'
        rows=tx.fetch_all(Query(f"SELECT description,goal FROM tasks WHERE id=$1 AND exploration_id=$2 AND status='{status}' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE",(task_id,exploration_id)))
        if admission is not None:admission.check(self.executor,task_id,exploration_id)
        if len(rows)!=1:raise BootstrapConflict('Task is not eligible for initialization')
        return rows[0]
    def claim(self,task_id,exploration_id,request_id,digest,owner,*,_admission=None):
        self._check();_positive(task_id);_positive(exploration_id)
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128 or not isinstance(digest,str) or len(digest)!=64 or not isinstance(owner,str) or len(owner)!=64:raise ValueError('Invalid bootstrap identity')
        with self.executor.transaction() as tx:
            description,goal=self._task(tx,task_id,exploration_id,_admission)
            digest=hashlib.sha256(json.dumps([digest,description,goal],ensure_ascii=False).encode()).hexdigest()
            rows=tx.fetch_all(Query(f'SELECT {COLS} FROM public.artex_task_bootstrap WHERE task_id=$1 FOR UPDATE',(task_id,)))
            if rows:
                if _admission is not None:raise BootstrapConflict('Admitted initialization cannot adopt an existing generation')
                receipt=_receipt(rows[0])
                if (receipt.exploration_id,receipt.request_id,receipt.config_digest)!=(exploration_id,request_id,digest) or receipt.phase!='prepared':raise BootstrapConflict('Existing initialization requires reconciliation')
                if self._goals(tx,exploration_id)!=receipt.goal_ids:raise BootstrapConflict('Prepared graph changed')
                return receipt,description,goal
            origin=tx.fetch_all(Query("SELECT id FROM exploration_nodes WHERE exploration_id=$1 AND kind='fact' AND state='origin' ORDER BY id LIMIT 2",(exploration_id,)))
            occupied=tx.fetch_all(Query("SELECT id FROM exploration_nodes WHERE exploration_id=$1 AND kind IN ('goal','intent') LIMIT 1",(exploration_id,)))
            if len(origin)!=1 or occupied:raise BootstrapConflict('Uninitialized graph with one origin required')
            row=tx.fetch_one(Query(f"INSERT INTO public.artex_task_bootstrap(task_id,exploration_id,request_id,config_digest,owner_token,phase) VALUES($1,$2,$3,$4,$5,'running') RETURNING {COLS}",(task_id,exploration_id,request_id,digest,owner)))
            if _admission is not None:_admission.check(self.executor,task_id,exploration_id)
            return _receipt(row),description,goal
    def verify(self,receipt):
        self._check()
        rows=self.executor.fetch_all(Query(f'SELECT {COLS} FROM public.artex_task_bootstrap WHERE task_id=$1',(receipt.task_id,)))
        return len(rows)==1 and _receipt(rows[0])==receipt and receipt.phase=='running'
    def _goals(self,tx,exploration_id):
        rows=tx.fetch_all(Query("SELECT n.id, EXISTS(SELECT 1 FROM exploration_edges e JOIN exploration_nodes o ON o.id=e.src_id AND o.exploration_id=e.exploration_id WHERE e.exploration_id=n.exploration_id AND e.dst_id=n.id AND e.rel='spawns' AND o.kind='fact' AND o.state='origin') FROM exploration_nodes n WHERE n.exploration_id=$1 AND n.kind='goal' ORDER BY n.id LIMIT 1001",(exploration_id,)))
        if not rows or len(rows)>1000 or any(row[1] is not True for row in rows):raise BootstrapUnresolved('Persisted goal lineage unavailable')
        return tuple(row[0] for row in rows)
    def finish(self,receipt,config_digest,*,authority,_admission=None):
        self._check()
        with self.executor.transaction() as tx:
            description,goal=self._task(tx,receipt.task_id,receipt.exploration_id,_admission)
            actual=hashlib.sha256(json.dumps([config_digest,description,goal],ensure_ascii=False).encode()).hexdigest()
            if actual!=receipt.config_digest:raise BootstrapConflict('Task input changed')
            ids=self._goals(tx,receipt.exploration_id)
            authority()
            returned=tx.fetch_all(Query(f"UPDATE public.artex_task_bootstrap SET phase='prepared',goal_ids=$1,revision=revision+1,updated_at=now() WHERE task_id=$2 AND run_id=$3 AND owner_token=$4 AND revision=$5 AND exploration_id=$6 AND request_id=$7 AND config_digest=$8 AND phase='running' RETURNING {COLS}",(list(ids),receipt.task_id,receipt.run_id,receipt.owner_token,receipt.revision,receipt.exploration_id,receipt.request_id,receipt.config_digest)))
            if len(returned)!=1:raise BootstrapUnresolved('Initialization owner changed')
            authority()
            return _receipt(returned[0])
    def unresolved(self,receipt):
        self._check()
        return self.executor.execute(Query("UPDATE public.artex_task_bootstrap SET phase='unresolved',revision=revision+1,updated_at=now() WHERE task_id=$1 AND run_id=$2 AND owner_token=$3 AND revision=$4 AND phase='running'",(receipt.task_id,receipt.run_id,receipt.owner_token,receipt.revision)))==1

class _InstalledAdmission:
    def __init__(self,handle):
        from .bootstrap_handle import BootstrapHandle
        if type(handle) is not BootstrapHandle:raise BootstrapConflict('Actual installed bootstrap handle required')
        self.handle=handle
    def check(self,executor,task_id,exploration_id):
        h=self.handle
        if h.executor is not executor or (h.task_id,str(h.exploration_id))!=(str(task_id),str(exploration_id)):
            raise BootstrapConflict('Admitted bootstrap scope mismatch')
        h._admitted_check()


class BootstrapCoordinator:
    def __init__(self,journal,*,planner_factory,lifecycle_guard,current):
        if not isinstance(journal,BootstrapJournal) or any(not callable(f) for f in (planner_factory,lifecycle_guard,current)):raise ValueError('Explicit journal, planner and lifetime authority required')
        self.journal=journal;self._journal=journal;self._executor=journal.executor
        self.factory,self.guard,self.current=planner_factory,lifecycle_guard,current
    def _check(self,task_id,exploration_id,revision):
        if self.journal is not self._journal or self.journal.executor is not self._executor or self.current(task_id,exploration_id,revision) is not True:raise BootstrapConflict('Initialization authority stale')
        if self.journal is not self._journal or self.journal.executor is not self._executor:raise BootstrapConflict('Initialization resource changed')
    def run(self,task_id,exploration_id,request_id,*,model,system,policy_revision,token):
        return self._run(task_id,exploration_id,request_id,model=model,system=system,policy_revision=policy_revision,token=token)
    def run_admitted(self,handle):
        admission=_InstalledAdmission(handle)
        if handle.coordinator is not self:raise BootstrapConflict('Exact owning coordinator required')
        admission.check(self._executor,int(handle.task_id),handle.exploration_id)
        return self._run(int(handle.task_id),handle.exploration_id,handle.request_id,
            model=handle.model,system=handle.system,policy_revision=handle.policy_revision,
            token=handle.token,_admission=admission)
    def _run(self,task_id,exploration_id,request_id,*,model,system,policy_revision,token,_admission=None):
        _positive(task_id);_positive(exploration_id)
        if not isinstance(policy_revision,str) or not policy_revision or len(policy_revision)>256 or not isinstance(system,str) or not system.strip():raise ValueError('Explicit immutable policy revision and prompt required')
        # policy_revision MUST identify provider/profile selection, tool policies,
        # constraint/scope approval callbacks, catalog and transcript configuration.
        digest=hashlib.sha256(json.dumps({'model':asdict(model),'system':system,'policy_revision':policy_revision},sort_keys=True,allow_nan=False).encode()).hexdigest()
        def check():
            self._check(task_id,exploration_id,policy_revision);token.check()
            if _admission is not None:_admission.check(self._executor,task_id,exploration_id)
        with self.guard(task_id,exploration_id):
            check()
            receipt,description,goal=self.journal.claim(task_id,exploration_id,request_id,digest,secrets.token_hex(32),_admission=_admission)
            check()
            if receipt.phase=='prepared':return receipt
        owner_thread=get_ident();active=False
        try:
            scope=GoalScope(task_id,exploration_id,receipt.run_id,policy_revision,model,system)
            def live():
                if not active or get_ident()!=owner_thread:raise BootstrapConflict('Bootstrap invocation authority ended')
            def owned(s):
                live()
                check()
                return s==scope and self.journal.verify(receipt)
            @contextmanager
            def guard(s):
                live()
                if s!=scope:raise BootstrapConflict('Foreign goal scope')
                with self.guard(task_id,exploration_id):
                    live()
                    yield
                    live()
            planner=self.factory(scope,owned,guard)
            if not isinstance(planner,GoalPlanner) or planner.store.executor is not self._executor or planner.store.exploration_id!=exploration_id or planner.current is not owned or planner.guard is not guard:raise BootstrapConflict('Exact owned GoalPlanner required')
            active=True
            try:
                outcome=planner.run(scope,request_id,goal,description,token=token)
            finally:
                active=False
            if not outcome.persisted or outcome.stale or outcome.result is None or outcome.result.reason!='completed':raise BootstrapUnresolved('Goal planning did not durably complete')
            with self.guard(task_id,exploration_id):
                check()
                def authority():
                    check()
                prepared=self.journal.finish(receipt,digest,authority=authority,_admission=_admission)
                authority()
                return prepared
        except BaseException:
            active=False
            try:self.journal.unresolved(receipt)
            except BaseException:pass
            raise BootstrapUnresolved('Initialization effects require reconciliation; do not retry') from None
        finally:
            active=False
