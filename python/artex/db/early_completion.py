"""Explicit timed ordinary completion, not a claim that every goal was achieved.

Go engine's frontier-drained branch tests no open goals and no open/running
intents. Empty/abandoned goals and paused intents therefore do not block it.
No evidence/proof requirement is invented here. Actual owned execution must also
be drained. All writers must honor the original lifetime barrier and task lock.
No automatic engine hook, positive admission, expiry takeover or retry exists.
"""
from dataclasses import dataclass,asdict
from datetime import datetime
import hashlib,json
from threading import RLock,get_ident
from contextlib import contextmanager
from ..agent.bootstrap import Receipt
from .core import Query
from .task_deadline import TaskDeadlineStore
from .schema import SchemaRefused,SchemaBootstrapError,IDENTITY_SQL,LOCK_KEY

class EarlyCompletionConflict(RuntimeError):pass
class EarlyCompletionUncertain(RuntimeError):pass

class EarlyCompletionGrant:
    """Host-created, scoped revocable permit; never execution authority.

    Revocation and acknowledgement are linearized by the private lock. Policy
    callbacks must return this exact instance; boolean approvals are unsupported.
    """
    def __setattr__(self,name,value):
        if hasattr(self,'_sealed'):raise AttributeError('Early completion grants are immutable; use revoke()')
        object.__setattr__(self,name,value)
    def __init__(self,receipt,*,operations=frozenset({'reserve','read','write'})):
        if type(receipt) is not Receipt or receipt.phase!='prepared' or type(operations) is not frozenset or not operations or not operations<=frozenset({'reserve','read','write'}):
            raise ValueError('Exact prepared receipt and finite operations required')
        self._receipt=receipt;self._identity=tuple(asdict(receipt).values());self._operations=operations
        self._lock=RLock();self._revoked=False;self._original=self;self._ack_thread=None;self._sealed=True
    def revoke(self):
        with self._lock:object.__setattr__(self,'_revoked',True)
    def _check(self,receipt,operation):
        if self._original is not self or self._revoked or receipt is not self._receipt or tuple(asdict(receipt).values())!=self._identity or operation not in self._operations:
            raise EarlyCompletionConflict('Early completion permit unavailable')
    def _validate(self,receipt,operation):
        with self._lock:self._check(receipt,operation)
    @contextmanager
    def _acknowledge(self,receipt):
        with self._lock:
            self._check(receipt,'write')
            if self._ack_thread is not None:raise EarlyCompletionConflict('Acknowledgement already active')
            object.__setattr__(self,'_ack_thread',get_ident())
            try:yield
            finally:object.__setattr__(self,'_ack_thread',None)

DDL="""CREATE TABLE public.artex_early_completion (
 task_id BIGINT NOT NULL REFERENCES public.tasks(id),
 exploration_id BIGINT NOT NULL REFERENCES public.explorations(id),
 run_id BIGINT NOT NULL, bootstrap_revision BIGINT NOT NULL,
 config_digest TEXT NOT NULL, first_run_at TIMESTAMPTZ NOT NULL,
 deadline_at TIMESTAMPTZ NOT NULL, timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds>0),
 graph_digest TEXT NOT NULL, status TEXT NOT NULL CHECK(status='done'),
 reason TEXT NOT NULL CHECK(reason='frontier_drained'), completed_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(task_id,run_id))"""

def install_early_completion(executor,*,expected_database,expected_schema,allow_create=False):
    if type(expected_database) is not str or not expected_database or expected_schema!='public' or allow_create is not True:
        raise SchemaRefused('Explicit named database/public fresh installation required')
    try:
        with executor.transaction() as tx:
            tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
            name,version,readonly,strings,temp=tx.fetch_one(Query(IDENTITY_SQL))
            if name!=expected_database or version<120000 or readonly!='off' or strings!='on' or temp!=0:raise SchemaRefused('Early completion database incompatible')
            tx.execute_script('SET LOCAL search_path = public, pg_catalog')
            rows=tx.fetch_one(Query("SELECT to_regclass('public.artex_early_completion'),to_regclass('public.artex_task_bootstrap'),to_regclass('public.tasks'),to_regclass('public.explorations')"))
            if rows[0] is not None or any(x is None for x in rows[1:]):raise SchemaRefused('Fresh receipt table and existing bootstrap schema required')
            tx.execute_script(DDL)
    except (SchemaRefused,SchemaBootstrapError):raise
    except Exception:raise SchemaBootstrapError('Early completion installation unconfirmed') from None

@dataclass(frozen=True)
class EarlyCompletionReceipt:
    task_id:int
    exploration_id:int
    run_id:int
    bootstrap_revision:int
    config_digest:str
    first_run_at:datetime
    deadline_at:datetime
    timeout_seconds:int
    graph_digest:str
    status:str
    reason:str
    completed_at:datetime

FIELDS=','.join(EarlyCompletionReceipt.__dataclass_fields__)

class EarlyCompletionStore:
    def __init__(self,deadline,*,grant,authorize):
        if type(deadline) is not TaskDeadlineStore or type(grant) is not EarlyCompletionGrant or not callable(authorize):raise ValueError('Exact prepared clock store and explicit completion grant required')
        self.deadline=deadline;self.lease=deadline.lease;self.authorize=authorize
        self.grant=grant;self._grant=grant
        grant._validate(deadline.receipt,'reserve')
        if self.lease is None:raise ValueError('Installed original clock lease required')
        self._pins=(deadline,self.lease,authorize,deadline.executor,deadline.guard,deadline.manager,deadline.handle,deadline.receipt,deadline._token)
        self._current=deadline.handle.coordinator.current
        self._used=False;self._cap=None;self._evidence=None;self._result=None
    def _check(self):
        d,l,a,e,g,m,h,r,t=self._pins
        if self.deadline is not d or self.lease is not l or self.authorize is not a or d.executor is not e or d.guard is not g or d.manager is not m or d.handle is not h or d.receipt is not r or d._token is not t or d.lease is not l:raise EarlyCompletionConflict('Early completion binding changed')
        if self.grant is not self._grant:raise EarlyCompletionConflict('Early permit changed')
        d._binding_check()
        if h.coordinator.current is not self._current:raise EarlyCompletionConflict('Configuration policy changed')
        if self._cap is not None and not m.validate_early_completion(self._cap,self):raise EarlyCompletionConflict('Early completion ownership changed')
    def _allow(self,operation):
        self._check()
        r=self._pins[7];h=self._pins[6]
        h.coordinator._check(r.task_id,r.exploration_id,h.policy_revision)
        self._check()
        if self._pins[2](self._pins[7],operation) is not self._grant:raise EarlyCompletionConflict('Early completion denied')
        h.coordinator._check(r.task_id,r.exploration_id,h.policy_revision)
        self._check();self._grant._validate(r,operation)
    def validate_reservation(self):
        self._allow('reserve')
        if self._pins[5].validate_deadline_owner(self.lease) is not True or self._pins[5].deadline_owner_phase(self.lease)!='attached':raise EarlyCompletionConflict('Attached original clock owner required')
        return True
    def _parent(self,tx,result=None):
        r=self._pins[7];c=self.lease.clock
        if result is None:
            rows=tx.fetch_all(Query("""SELECT first_run_at,deadline_at,timeout_seconds FROM tasks WHERE id=$1 AND exploration_id=$2
AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND deadline_at>clock_timestamp() AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE""",(r.task_id,r.exploration_id)))
            if rows!=[(c.first_run_at,c.deadline_at,c.timeout_seconds)]:raise EarlyCompletionConflict('Unexpired running clock unavailable')
        else:
            rows=tx.fetch_all(Query("""SELECT status,completed_at,first_run_at,deadline_at,timeout_seconds FROM tasks WHERE id=$1 AND exploration_id=$2
AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE""",(r.task_id,r.exploration_id)))
            if rows!=[('done',result.completed_at,c.first_run_at,c.deadline_at,c.timeout_seconds)]:raise EarlyCompletionConflict('Committed terminal task changed')
        rows=tx.fetch_all(Query("""SELECT 1 FROM public.artex_task_bootstrap WHERE task_id=$1 AND exploration_id=$2
AND run_id=$3 AND request_id=$4 AND config_digest=$5 AND owner_token=$6 AND revision=$7 AND phase='prepared' FOR SHARE""",
            (r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision)))
        if rows!=[(1,)]:raise EarlyCompletionConflict('Prepared generation changed')
        if result is not None:
            rows=tx.fetch_all(Query(f'SELECT {FIELDS} FROM public.artex_early_completion WHERE task_id=$1 AND run_id=$2 FOR SHARE',(r.task_id,r.run_id)))
            if rows!=[tuple(asdict(result).values())]:raise EarlyCompletionConflict('Committed early receipt changed')
    def _frontier(self,tx):
        # Constant-size projection per row; no payloads, source expansion or proof
        # interpretation. Parent locking serializes participating graph writers.
        rows=tx.fetch_all(Query("SELECT id,kind,CASE WHEN octet_length(state)<=32 THEN state ELSE NULL END AS state FROM exploration_nodes WHERE exploration_id=$1 AND kind IN ('goal','intent') ORDER BY id LIMIT 10001 FOR SHARE",(self._pins[7].exploration_id,)))
        if len(rows)>10000:raise EarlyCompletionConflict('Frontier exceeds bound')
        seen=set()
        for id,kind,state in rows:
            if type(id) is not int or id<=0 or id in seen or kind not in ('goal','intent') or type(state) is not str or len(state)>32:raise EarlyCompletionConflict('Invalid frontier')
            seen.add(id)
            if (kind=='goal' and state=='open') or (kind=='intent' and state in ('open','running')):raise EarlyCompletionConflict('Frontier remains active')
        return hashlib.sha256(json.dumps(rows,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    def _verify(self,tx,result):
        self._parent(tx,result)
        if self._frontier(tx)!=result.graph_digest:raise EarlyCompletionConflict('Frontier changed')
    def settle(self):
        if self._used:raise EarlyCompletionConflict('Early completion cannot retry')
        self._used=True;attempted=False;m=self._pins[5];r=self._pins[7];c=self.lease.clock
        try:
            self._cap=m.reserve_early_completion(self)
            with self._pins[4](r.task_id,r.exploration_id):
                self._allow('read')
                with self._pins[3].transaction() as tx:
                    self._parent(tx)
                    if tx.fetch_all(Query('SELECT task_id FROM public.artex_early_completion WHERE task_id=$1 AND run_id=$2 FOR UPDATE',(r.task_id,r.run_id))):raise EarlyCompletionConflict('Existing receipt cannot be adopted')
                    digest=self._frontier(tx);self._allow('write')
                    self._parent(tx)
                    if self._frontier(tx)!=digest:raise EarlyCompletionConflict('Frontier changed after grant')
                    attempted=True
                    rows=tx.fetch_all(Query("""UPDATE tasks SET status='done',completed_at=COALESCE(completed_at,now())
WHERE id=$1 AND exploration_id=$2 AND status='running' AND NOT paused AND NOT queued
AND deleted_at IS NULL AND archived_at IS NULL AND first_run_at=$3 AND deadline_at=$4 AND timeout_seconds=$5
AND deadline_at>clock_timestamp() AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) RETURNING completed_at""",
                        (r.task_id,r.exploration_id,c.first_run_at,c.deadline_at,c.timeout_seconds)))
                    if len(rows)!=1 or len(rows[0])!=1 or type(rows[0][0]) is not datetime or rows[0][0].tzinfo is None:raise EarlyCompletionConflict('Early terminal CAS failed')
                    result=EarlyCompletionReceipt(r.task_id,r.exploration_id,r.run_id,r.revision,r.config_digest,c.first_run_at,c.deadline_at,c.timeout_seconds,digest,'done','frontier_drained',rows[0][0])
                    values=tuple(asdict(result).values())
                    if tx.fetch_all(Query(f'INSERT INTO public.artex_early_completion({FIELDS}) VALUES('+','.join('$'+str(i) for i in range(1,13))+f') RETURNING {FIELDS}',values))!=[values]:raise EarlyCompletionConflict('Early receipt write failed')
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
                result=m.confirm_early_completion(self._cap,self,self._evidence)
            m.finish_early_completion_cancellation(self._cap,self)
            return result
        except BaseException:
            if self._cap is not None:m.quarantine_early_completion(self._cap,self)
            if attempted:raise EarlyCompletionUncertain('Early completion unconfirmed; ownership retained, do not retry') from None
            raise EarlyCompletionConflict('Early completion refused; ownership retained') from None
    def consume_evidence(self,cap,evidence):
        self._check()
        if self._grant._ack_thread!=get_ident():raise EarlyCompletionConflict('Locked acknowledgement required')
        self._grant._check(self._pins[7],'write')
        if cap is not self._cap or evidence is not self._evidence or evidence is None or self._result is None:raise EarlyCompletionConflict('Exact private committed evidence required')
        result=self._result;self._evidence=None;return result
