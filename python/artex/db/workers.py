"""Task-scoped persisted worker-intent boundary, not a worker execution engine.

Upstream has no workers table: worker identity is an exploration intent and its
stable norma transcript key is exp{exploration}-worker-i{intent}. State, payload,
owner and content_version live in exploration_nodes. This facade reads/creates
those rows and implements version+state CAS; claim also increments content_version
(additional ABA protection versus upstream ClaimIntent). All writers must honor
this version for the generation guarantee. Existing ExplorationStore.claim_intent
does not bump content_version, and generic graph writers do not share this
injected lifetime guard. Do not mix those writers with this facade when relying
on its generation/lifetime fencing. No distributed runtime lease is implied.

Every operation requires an injected lifetime_guard(task_id, exploration_id, write)
context manager sharing the task deletion/archive barrier. A transaction locks the
live parent task and rejects any archive operation row, including failed states
(conservative subset). Reads never resolve inherited tasks or archives implicitly.

Transcript backend read(session, limit) and append(session, data, version) are
explicit adapters, NOT SQL or an implementation of norma JSONL. Calls hold the
same task guard (node locking differs for reads and appends; see below). The backend must enforce generation/idempotency and
path safety. Transcript reads hold the task guard/parent lock but do not lock
the node; appends additionally acquire the node FOR UPDATE lock. A filesystem append is NOT atomic with PostgreSQL commit: a later DB
failure may leave a durable append. Caller must reconcile rather than blindly
retry. Missing adapters fail closed. No model/tool/network or file I/O is created.
Destructive deletion, reset, payload edits, anchors, export/archive and resume
cleanup remain unsupported instead of approximating their cascade semantics.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from .core import Query,int64
from .exploration import Node,NODE_COLS,_json


class WorkerUnavailable(RuntimeError): pass
class WorkerMissing(LookupError): pass
class WorkerConflict(RuntimeError): pass

STATES=frozenset(('open','running','paused','done','blocked','exhausted','stopped'))
TERMINAL=frozenset(('done','blocked','exhausted','stopped'))


def _id(value):
    if int64(value,'ID')<=0: raise ValueError('ID must be positive')
    return value


def _version(value):
    if type(value) is not int or not 0<=value<2**31: raise ValueError('invalid content version')
    return value


def _text(value):
    if not isinstance(value,str) or '\x00' in value: raise ValueError('invalid worker text')
    value.encode('utf-8');return value


@dataclass(frozen=True)
class WorkerRecord:
    node:Node
    version:int
    @property
    def id(self): return self.node.id


class WorkerStore:
    def __init__(self,executor,task_id,exploration_id,*,lifetime_guard=None,transcripts=None):
        self.executor=executor;self.task_id=_id(task_id);self.exploration_id=_id(exploration_id)
        self.lifetime_guard=lifetime_guard;self.transcripts=transcripts

    @contextmanager
    def _operation(self,write=False):
        if not callable(self.lifetime_guard) or not callable(getattr(self.executor,'transaction',None)):
            raise WorkerUnavailable('Task lifetime guard and transaction adapter required')
        with self.lifetime_guard(self.task_id,self.exploration_id,write):
            with self.executor.transaction() as tx:
                rows=tx.fetch_all(Query('''SELECT id FROM tasks
WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1)
FOR '''+('UPDATE' if write else 'SHARE'),(self.task_id,self.exploration_id)))
                if rows!=[(self.task_id,)] and rows!=[[self.task_id]]:
                    raise WorkerMissing('Live unarchived parent task unavailable')
                yield tx

    def _record(self,row):
        if len(row)!=11: raise WorkerUnavailable('Invalid worker record')
        node=Node(*row[:10]);version=_version(row[10])
        if node.kind!='intent': raise WorkerUnavailable('Unexpected non-intent worker')
        return WorkerRecord(node,version)

    def _get(self,tx,worker_id,*,locked=False):
        rows=tx.fetch_all(Query('SELECT '+NODE_COLS+", content_version FROM exploration_nodes WHERE exploration_id=$1 AND id=$2 AND kind='intent'"+(' FOR UPDATE' if locked else ''),(self.exploration_id,_id(worker_id))))
        if not rows: return None
        if len(rows)!=1: raise WorkerUnavailable('Duplicate worker identity')
        return self._record(rows[0])

    def get(self,worker_id):
        _id(worker_id)
        with self._operation() as tx:return self._get(tx,worker_id)

    def list(self,*,before=0,limit=100):
        if int64(before,'before')<0 or type(limit) is not int or not 1<=limit<=500: raise ValueError('invalid pagination')
        with self._operation() as tx:
            rows=tx.fetch_all(Query('SELECT '+NODE_COLS+", content_version FROM exploration_nodes WHERE exploration_id=$1 AND kind='intent' AND ($2=0 OR id<$2) ORDER BY id DESC LIMIT $3",(self.exploration_id,before,limit)))
            return [self._record(row) for row in rows]

    def create(self,payload,*,priority=0,origin='planner'):
        raw=_json(payload);_text(origin)
        if type(priority) is not int or not -(2**31)<=priority<2**31: raise ValueError('invalid priority')
        with self._operation(True) as tx:
            row=tx.fetch_one(Query("INSERT INTO exploration_nodes(exploration_id,kind,payload,priority,state,origin) VALUES ($1,'intent',$2,$3,'open',$4) RETURNING id",(self.exploration_id,raw,priority,origin or 'planner')))
            if not row or len(row)!=1: raise WorkerUnavailable('Invalid worker creation result')
            return _id(row[0])

    def transition(self,worker_id,expected_state,new_state,expected_version):
        _id(worker_id);_version(expected_version)
        if expected_state not in STATES or new_state not in STATES: raise ValueError('invalid worker state')
        if expected_state in TERMINAL:
            raise WorkerUnavailable('Terminal worker reopening requires unported resume cleanup')
        with self._operation(True) as tx:
            count=tx.execute(Query('''UPDATE exploration_nodes SET state=$1, blocked_reason=NULL,
content_version=content_version+1, completed_at=CASE WHEN $6 THEN now() ELSE NULL END
WHERE id=$2 AND exploration_id=$3 AND kind='intent' AND state=$4 AND content_version=$5''',
                (new_state,worker_id,self.exploration_id,expected_state,expected_version,new_state in TERMINAL)))
            if type(count) is not int or count not in (0,1): raise WorkerUnavailable('Invalid transition result')
            return count==1

    def claim(self,worker_id,owner,expected_version):
        _id(worker_id);_version(expected_version);_text(owner)
        if not owner: raise ValueError('owner required')
        with self._operation(True) as tx:
            count=tx.execute(Query("UPDATE exploration_nodes SET state='running', owner=$1, content_version=content_version+1 WHERE id=$2 AND exploration_id=$3 AND kind='intent' AND state='open' AND content_version=$4",(owner,worker_id,self.exploration_id,expected_version)))
            if type(count) is not int or count not in (0,1): raise WorkerUnavailable('Invalid claim result')
            return count==1

    def _session(self,worker_id):return f'exp{self.exploration_id}-worker-i{_id(worker_id)}'

    def read_transcript(self,worker_id,*,limit=1000):
        _id(worker_id)
        if type(limit) is not int or not 1<=limit<=10000: raise ValueError('invalid transcript limit')
        if not callable(getattr(self.transcripts,'read',None)): raise WorkerUnavailable('Transcript backend required')
        with self._operation() as tx:
            if self._get(tx,worker_id) is None: raise WorkerMissing('Worker not found')
            return self.transcripts.read(self._session(worker_id),limit)

    def append_transcript(self,worker_id,data,*,expected_version):
        _id(worker_id);_version(expected_version)
        raw=_json(data)
        if len(raw.encode('utf-8'))>1048576: raise ValueError('transcript entry too large')
        if not callable(getattr(self.transcripts,'append',None)): raise WorkerUnavailable('Transcript backend required')
        with self._operation(True) as tx:
            record=self._get(tx,worker_id,locked=True)
            if record is None: raise WorkerMissing('Worker not found')
            if record.version!=expected_version or record.node.state!='running': raise WorkerConflict('Worker generation/state changed')
            return self.transcripts.append(self._session(worker_id),raw,expected_version)

    def delete(self,*args,**kwargs):raise WorkerUnavailable('Worker cancellation/cascade cleanup is not ported')
    def archive(self,*args,**kwargs):raise WorkerUnavailable('Worker archive/export is not ported')
