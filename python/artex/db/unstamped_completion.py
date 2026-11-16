"""Standalone unstamped frontier completion; no lease or execution-clock fabrication.

An exact prepared positive-timeout owner can finish before its first real dispatch.
Both clock columns must remain NULL. Source ordinary frontier semantics apply;
no proof-only achievement assertion, positive admission or automatic host hook.
All graph writers must honor the original guard and task lock. Postcommit doubt
retains ownership and never authorizes model/write replay. Invocation lifetime
is not yet tracked by ManualActionOwner; standalone calls do not prove shutdown
safety through their post-retirement cancellation tail.
"""
from dataclasses import dataclass,asdict
from datetime import datetime
from threading import get_ident
import hashlib,json
from .core import Query
from .task_deadline import TaskDeadlineStore
from .early_completion import EarlyCompletionGrant
from .schema import SchemaRefused,SchemaBootstrapError,IDENTITY_SQL,LOCK_KEY

class UnstampedCompletionConflict(RuntimeError):pass
class UnstampedCompletionUncertain(RuntimeError):pass

DDL="""CREATE TABLE public.artex_unstamped_completion (
 task_id BIGINT NOT NULL REFERENCES public.tasks(id),
 exploration_id BIGINT NOT NULL REFERENCES public.explorations(id),
 run_id BIGINT NOT NULL, bootstrap_revision BIGINT NOT NULL,
 config_digest TEXT NOT NULL, clock_state TEXT NOT NULL CHECK(clock_state='unstamped'),
 timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds>0),
 graph_digest TEXT NOT NULL, status TEXT NOT NULL CHECK(status='done'),
 reason TEXT NOT NULL CHECK(reason='frontier_drained'), completed_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(task_id,run_id))"""

def install_unstamped_completion(executor,*,expected_database,expected_schema,allow_create=False):
    if type(expected_database) is not str or not expected_database or expected_schema!='public' or allow_create is not True:
        raise SchemaRefused('Explicit named database/public fresh installation required')
    try:
        with executor.transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
            name,version,readonly,strings,temp=tx.fetch_one(Query(IDENTITY_SQL))
            if name!=expected_database or version<120000 or readonly!='off' or strings!='on' or temp!=0:raise SchemaRefused('Early completion database incompatible')
            tx.execute_script('SET LOCAL search_path = public, pg_catalog')
            rows=tx.fetch_one(Query("SELECT to_regclass('public.artex_unstamped_completion'),to_regclass('public.artex_task_bootstrap'),to_regclass('public.tasks'),to_regclass('public.explorations')"))
            if rows[0] is not None or any(x is None for x in rows[1:]):raise SchemaRefused('Fresh receipt table and existing bootstrap schema required')
            tx.execute_script(DDL)
    except (SchemaRefused,SchemaBootstrapError):raise
    except Exception:raise SchemaBootstrapError('Early completion installation unconfirmed') from None

@dataclass(frozen=True)
class UnstampedCompletionReceipt:
    task_id:int
    exploration_id:int
    run_id:int
    bootstrap_revision:int
    config_digest:str
    clock_state:str
    timeout_seconds:int
    graph_digest:str
    status:str
    reason:str
    completed_at:datetime

FIELDS=','.join(UnstampedCompletionReceipt.__dataclass_fields__)

class UnstampedCompletionStore:
    def __init__(self,deadline,*,grant,authorize):
        if type(deadline) is not TaskDeadlineStore or type(grant) is not EarlyCompletionGrant or not callable(authorize):raise ValueError('Exact prepared clock store and explicit completion grant required')
        self.deadline=deadline;self.authorize=authorize
        self.timeout=deadline.handle.prepared_timeout_seconds
        if type(self.timeout) is not int or not 0<self.timeout<=2**31-1:raise ValueError('Positive prepared timeout required')
        self.grant=grant;self._grant=grant
        grant._validate(deadline.receipt,'reserve')
        if deadline.lease is not None:raise ValueError('Unstamped original owner required')
        self._pins=(deadline,self.timeout,authorize,deadline.executor,deadline.guard,deadline.manager,deadline.handle,deadline.receipt,deadline._token)
        self._current=deadline.handle.coordinator.current
        self._used=False;self._cap=None;self._evidence=None;self._result=None
    def _check(self):
        d,timeout,a,e,g,m,h,r,t=self._pins
        if self.deadline is not d or self.timeout!=timeout or type(self.timeout) is not int or self.authorize is not a or d.executor is not e or d.guard is not g or d.manager is not m or d.handle is not h or d.receipt is not r or d._token is not t or d.lease is not None or h.prepared_timeout_seconds!=timeout:raise UnstampedCompletionConflict('Early completion binding changed')
        if self.grant is not self._grant:raise UnstampedCompletionConflict('Early permit changed')
        d._binding_check()
        if h.coordinator.current is not self._current:raise UnstampedCompletionConflict('Configuration policy changed')
        if self._cap is not None and not m.validate_unstamped_completion(self._cap,self):raise UnstampedCompletionConflict('Early completion ownership changed')
    def _allow(self,operation):
        self._check()
        r=self._pins[7];h=self._pins[6]
        h.coordinator._check(r.task_id,r.exploration_id,h.policy_revision)
        self._check()
        if self._pins[2](self._pins[7],operation) is not self._grant:raise UnstampedCompletionConflict('Early completion denied')
        h.coordinator._check(r.task_id,r.exploration_id,h.policy_revision)
        self._check();self._grant._validate(r,operation)
    def validate_reservation(self):
        self._allow('reserve')
        if self._pins[5].owns_generation(str(self._pins[7].task_id),self._pins[8],self._pins[6]) is not True:raise UnstampedCompletionConflict('Original unstamped owner required')
        return True
    def _parent(self,tx,result=None):
        r=self._pins[7]
        if result is None:
            rows=tx.fetch_all(Query("""SELECT first_run_at,deadline_at,timeout_seconds FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND first_run_at IS NULL AND deadline_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE""",(r.task_id,r.exploration_id)))
            if rows!=[(None,None,self.timeout)]:raise UnstampedCompletionConflict('Exact unstamped running task unavailable')
        else:
            rows=tx.fetch_all(Query("""SELECT status,completed_at,first_run_at,deadline_at,timeout_seconds FROM tasks WHERE id=$1 AND exploration_id=$2
AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE""",(r.task_id,r.exploration_id)))
            if rows!=[('done',result.completed_at,None,None,self.timeout)]:raise UnstampedCompletionConflict('Committed terminal task changed')
        rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",
            (r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
        if rows!=[(1,)]:raise UnstampedCompletionConflict('Prepared generation changed')
        if result is not None:
            rows=tx.fetch_all(Query(f'SELECT {FIELDS} FROM public.artex_unstamped_completion WHERE task_id=$1 AND run_id=$2 FOR SHARE',(r.task_id,r.run_id)))
            if rows!=[tuple(asdict(result).values())]:raise UnstampedCompletionConflict('Committed early receipt changed')
    def _frontier(self,tx):
        # Constant-size projection per row; no payloads, source expansion or proof
        # interpretation. Parent locking serializes participating graph writers.
        rows=tx.fetch_all(Query("SELECT id,kind,CASE WHEN octet_length(state)<=32 THEN state ELSE NULL END AS state FROM exploration_nodes WHERE exploration_id=$1 AND kind IN ('goal','intent') ORDER BY id LIMIT 10001 FOR SHARE",(self._pins[7].exploration_id,)))
        if len(rows)>10000:raise UnstampedCompletionConflict('Frontier exceeds bound')
        seen=set()
        for id,kind,state in rows:
            if type(id) is not int or id<=0 or id in seen or kind not in ('goal','intent') or type(state) is not str or len(state)>32:raise UnstampedCompletionConflict('Invalid frontier')
            seen.add(id)
            if (kind=='goal' and state=='open') or (kind=='intent' and state in ('open','running')):raise UnstampedCompletionConflict('Frontier remains active')
        return hashlib.sha256(json.dumps(rows,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    def _verify(self,tx,result):
        self._parent(tx,result)
        if self._frontier(tx)!=result.graph_digest:raise UnstampedCompletionConflict('Frontier changed')
    def settle(self):
        if self._used:raise UnstampedCompletionConflict('Early completion cannot retry')
        self._used=True;attempted=False;m=self._pins[5];r=self._pins[7]
        try:
            self._cap=m.reserve_unstamped_completion(self)
            with self._pins[4](r.task_id,r.exploration_id):
                self._allow('read')
                with self._pins[3].transaction() as tx:
                    self._parent(tx)
                    if tx.fetch_all(Query('SELECT task_id FROM public.artex_unstamped_completion WHERE task_id=$1 AND run_id=$2 FOR UPDATE',(r.task_id,r.run_id))):raise UnstampedCompletionConflict('Existing receipt cannot be adopted')
                    digest=self._frontier(tx);self._allow('write')
                    self._parent(tx)
                    if self._frontier(tx)!=digest:raise UnstampedCompletionConflict('Frontier changed after grant')
                    attempted=True
                    rows=tx.fetch_all(Query("""UPDATE tasks SET status='done',completed_at=COALESCE(completed_at,now())
WHERE id=$1 AND exploration_id=$2 AND status='running' AND NOT paused AND NOT queued
AND deleted_at IS NULL AND archived_at IS NULL AND first_run_at IS NULL AND deadline_at IS NULL AND timeout_seconds=$3
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) RETURNING completed_at""",
                        (r.task_id,r.exploration_id,self.timeout)))
                    if len(rows)!=1 or len(rows[0])!=1 or type(rows[0][0]) is not datetime or rows[0][0].tzinfo is None:raise UnstampedCompletionConflict('Early terminal CAS failed')
                    result=UnstampedCompletionReceipt(r.task_id,r.exploration_id,r.run_id,r.revision,r.config_digest,'unstamped',self.timeout,digest,'done','frontier_drained',rows[0][0])
                    values=tuple(asdict(result).values())
                    if tx.fetch_all(Query(f'INSERT INTO public.artex_unstamped_completion({FIELDS}) VALUES('+','.join('$'+str(i) for i in range(1,12))+f') RETURNING {FIELDS}',values))!=[values]:raise UnstampedCompletionConflict('Early receipt write failed')
                    self._allow('write');self._verify(tx,result)
                self._allow('write');self._verify(self._pins[3],result)
            self._allow('write');self._verify(self._pins[3],result)
            # Readback is an external adapter boundary too: it cannot revoke the
            # original grant and still yield a consumable retirement receipt.
            self._allow('write')
            # Final authority callbacks may themselves change durable state.
            # Verify task, prepared identity, exact receipt and frontier after
            # that boundary before exposing private commit evidence. This is a
            # finite boundary check, not a fixed point over arbitrary callbacks.
            self._verify(self._pins[3],result);self._check()
            self._result=result;self._evidence=object()
            # No SQL or policy/config callbacks inside this critical section.
            with self._grant._acknowledge(r):
                self._check()
                result=m.confirm_unstamped_completion(self._cap,self,self._evidence)
            m.finish_unstamped_completion_cancellation(self._cap,self)
            return result
        except BaseException:
            if self._cap is not None:m.quarantine_unstamped_completion(self._cap,self)
            if attempted:raise UnstampedCompletionUncertain('Early completion unconfirmed; ownership retained, do not retry') from None
            raise UnstampedCompletionConflict('Early completion refused; ownership retained') from None
    def consume_evidence(self,cap,evidence):
        self._check()
        if self._grant._ack_thread!=get_ident():raise UnstampedCompletionConflict('Locked acknowledgement required')
        self._grant._check(self._pins[7],'write')
        if cap is not self._cap or evidence is not self._evidence or evidence is None or self._result is None:raise UnstampedCompletionConflict('Exact private committed evidence required')
        result=self._result;self._evidence=None;return result
