"""Scoped task activity persistence, based on db/exploration.go.

Exact source activity columns, no conversation title/touch or transcript mapping.
Mandatory host guard/current closures must bind the same task/exploration/run/
segment lifetime as the caller. SQL locking protects row ownership, NOT runtime
lifetime. Host must cancel/drain before clear/archive/deletion; an archive_failed
row permits writes, other archive states do not. No distributed JSONL atomicity,
retry, idempotency, inherited-trace expansion or lifecycle ownership is invented.
Metadata is a structured JSON value (None means source default {}), not raw JSON
text. All rows in a bounded append batch commit together. Reads exclude deleted
parents; inherited/archive read policy remains the caller's authorization duty.
"""
import json
from copy import deepcopy
from .core import Query
from .postgres import NoRowsError
from .conversation_activity import Activity,_id,_text,_tokens,_limit

class TaskActivityInvalid(ValueError):pass
class TaskActivityMissing(LookupError):pass
class TaskActivityUnavailable(RuntimeError):pass

COLUMNS="a.id,a.node_id,COALESCE(a.worker,''),COALESCE(a.kind,''),COALESCE(a.tool,''),COALESCE(a.tool_use_id,''),a.is_error,COALESCE(a.summary,''),a.metadata,a.created_at,a.input_tokens,a.output_tokens,a.cache_read_tokens,a.cache_write_tokens,a.main_seg"

def _values(item):
    if not isinstance(item,Activity) or type(item.is_error) is not bool:raise TaskActivityInvalid('Valid Activity required')
    node=None if item.node_id is None else _id(item.node_id,'node ID')
    seg=item.main_seg
    if seg is not None and (type(seg) is not int or not 0<=seg<2**31):raise TaskActivityInvalid('Invalid main segment')
    worker=_text(item.worker)
    if seg is not None and worker!='mainagent':raise TaskActivityInvalid('Main segment requires mainagent worker')
    try:
        metadata=json.dumps({} if item.metadata is None else item.metadata,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        metadata.encode('utf-8')
        def nul(value):
            if isinstance(value,str):return '\x00' in value
            if isinstance(value,dict):return any(nul(k) or nul(v) for k,v in value.items())
            if isinstance(value,(list,tuple)):return any(nul(v) for v in value)
            return False
        if nul(item.metadata):raise ValueError()
    except (ValueError,TypeError,UnicodeError):raise TaskActivityInvalid('Invalid JSON metadata') from None
    return (node,worker,*(_text(getattr(item,n)) for n in ('kind','tool','tool_use_id')),item.is_error,
            _text(item.summary),_text(item.detail),metadata,*(_tokens(getattr(item,n)) for n in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')),seg)

def _row(row):
    if len(row)!=15:raise TaskActivityUnavailable('Invalid activity row')
    return Activity(id=row[0],node_id=row[1],worker=row[2],kind=row[3],tool=row[4],tool_use_id=row[5],is_error=row[6],summary=row[7],metadata=deepcopy(row[8]),created_at=row[9],input_tokens=row[10],output_tokens=row[11],cache_read_tokens=row[12],cache_write_tokens=row[13],main_seg=row[14])

class TaskActivityStore:
    def __init__(self,executor,task_id,exploration_id,*,lifecycle_guard,current,scope_identity):
        self.executor=executor;self.task_id=_id(task_id,'task ID');self.exploration_id=_id(exploration_id,'exploration ID')
        if not callable(lifecycle_guard) or not callable(current):raise TaskActivityInvalid('Explicit lifecycle guard/current required')
        if not isinstance(scope_identity,dict) or not scope_identity:raise TaskActivityInvalid('Explicit scope identity required')
        self.scope_identity=deepcopy(scope_identity)
        self.guard,self.current=lifecycle_guard,current
    def _check(self):
        if self.current() is not True:raise TaskActivityUnavailable('Task activity lifetime changed')
    def append_many(self,items):
        if not isinstance(items,(list,tuple)) or len(items)>10000:raise TaskActivityInvalid('Bounded activity batch required')
        values=[_values(item) for item in deepcopy(items)]
        transaction=getattr(self.executor,'transaction',None)
        if not callable(transaction):raise TaskActivityUnavailable('Transaction adapter required')
        with self.guard():
            self._check()
            with transaction() as tx:
                try:parent=tx.fetch_one(Query('SELECT exploration_id,deleted_at FROM tasks WHERE id=$1 FOR UPDATE',(self.task_id,)))
                except NoRowsError:parent=None
                if parent is None or parent[1] is not None or parent[0]!=self.exploration_id:raise TaskActivityMissing('Task exploration unavailable')
                archive=tx.fetch_all(Query('SELECT state FROM task_archives WHERE task_id=$1',(self.task_id,)))
                if archive and archive[0][0]!='archive_failed':raise TaskActivityUnavailable('Task archive blocks writes')
                ids=[]
                for fields in values:
                    if fields[0] is not None:
                        rows=tx.fetch_all(Query('SELECT id FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 FOR KEY SHARE',(fields[0],self.exploration_id)))
                        if not rows:raise TaskActivityMissing('Node outside exploration')
                    row=tx.fetch_one(Query('''INSERT INTO activity(exploration_id,node_id,worker,kind,tool,tool_use_id,is_error,summary,detail,metadata,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,main_seg)
VALUES ($1,$2,NULLIF($3,''),NULLIF($4,''),NULLIF($5,''),NULLIF($6,''),$7,NULLIF($8,''),NULLIF($9,''),$10::jsonb,$11,$12,$13,$14,$15) RETURNING id''',(self.exploration_id,*fields)))
                    ids.append(row[0])
                self._check()
                return ids
    def list(self,node_id=None,since_id=0,limit=300):
        node=None if node_id is None else _id(node_id,'node ID');since_id=_id(since_id,'since ID',False);limit=_limit(limit,300)
        with self.guard():
            self._check()
            rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+''' FROM activity a JOIN tasks t ON t.exploration_id=a.exploration_id
WHERE t.id=$1 AND t.deleted_at IS NULL AND a.exploration_id=$2 AND ($3::bigint IS NULL OR a.node_id=$3) AND a.id>$4 ORDER BY a.id LIMIT $5''',(self.task_id,self.exploration_id,node,since_id,limit)))
            self._check();items=[_row(row) for row in rows]
            return items,max([since_id]+[item.id for item in items])
    def detail(self,id):
        id=_id(id,'activity ID')
        with self.guard():
            self._check()
            rows=self.executor.fetch_all(Query('''SELECT a.detail FROM activity a JOIN tasks t ON t.exploration_id=a.exploration_id
WHERE a.id=$1 AND a.exploration_id=$2 AND t.id=$3 AND t.deleted_at IS NULL''',(id,self.exploration_id,self.task_id)))
            self._check();return (rows[0][0] or '') if rows else ''
