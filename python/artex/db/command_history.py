"""Read-only source command history: activity tool_use, never shell execution.

?task in the original HTTP contract means exploration ID, NOT tasks.id. Scope
binds both IDs separately. ILIKE wildcard semantics for q match tool/input only.
Raw inputs/outputs may be secrets; an explicit separate raw_authorize is required.
Unlike source's unsafe global tool_use_id join, pairing additionally requires
same exploration, worker, node and main segment, nonempty call ID. Reused same-session call IDs or multiple exact
results reject the whole read, not arbitrary selection/duplicate counts. Missing
result remains empty output/nonerror as source. Archives unsupported/failclosed.
One SQL statement includes archive state, counts, page and ambiguity sentinels.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from .core import Query,int64
from .token_usage import _integer
class CommandDenied(PermissionError):pass
class CommandUnavailable(RuntimeError):pass
@dataclass(frozen=True)
class CommandScope:
    workspace:str
    task_id:int|None=None
    exploration_id:int|None=None

class CommandHistoryStore:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,raw_authorize=None,max_rows=1000,max_bytes=1048576):
        if type(scope) is not CommandScope or not isinstance(scope.workspace,str) or not 1<=len(scope.workspace)<=256:raise ValueError('Explicit command scope required')
        if (scope.task_id is None)!=(scope.exploration_id is None):raise ValueError('Exact task/exploration required')
        for v in (scope.task_id,scope.exploration_id):
            if v is not None and int64(v,'command scope')<=0:raise ValueError('Positive scope required')
        if any(not callable(v) for v in (authorize,current,lifecycle_guard)):raise ValueError('Explicit read policies required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=16777216:raise ValueError('Invalid history bounds')
        self.executor,self.scope=executor,scope;self._executor,self._scope=executor,scope
        self.authorize,self.current,self.guard,self.raw=authorize,current,lifecycle_guard,raw_authorize
        self.max_rows,self.max_bytes=max_rows,max_bytes
    def _check(self):
        if self.executor is not self._executor or self.scope is not self._scope:raise CommandDenied()
        if self.current(self._scope) is not True:raise CommandDenied()
        if self.executor is not self._executor or self.scope is not self._scope:raise CommandDenied()
    @contextmanager
    def _read(self,op,q):
        with self.guard(self._scope):
            self._check()
            if self.authorize(self._scope,op,q) is not True:raise CommandDenied()
            self._check()
            if op=='list':
                if not callable(self.raw) or self.raw(self._scope,q) is not True:raise CommandDenied()
                self._check()
            with self._executor.transaction() as tx:
                if self._scope.task_id is not None:
                    rows=tx.fetch_all(Query('SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE',(self._scope.task_id,self._scope.exploration_id)))
                    if rows!=[(self._scope.task_id,)]:raise CommandUnavailable('Task history unavailable')
                self._check();yield tx;self._check()
            self._check()
    def _base(self,q):
        if not isinstance(q,str) or '\0' in q or len(q.encode('utf-8'))>4096:raise ValueError('Bounded keyword required')
        exp=self._scope.exploration_id
        return """WITH archive_state AS (SELECT EXISTS(SELECT 1 FROM task_archives WHERE $1::bigint IS NULL OR task_id=$1) AS blocked), filtered AS (
SELECT u.* FROM activity u WHERE u.kind='tool_use' AND ($2::bigint IS NULL OR u.exploration_id=$2)
AND ($3='' OR u.tool ILIKE $4 OR u.detail ILIKE $4)), paired AS (
SELECT u.id,u.exploration_id,COALESCE(u.worker,'') AS worker,COALESCE(u.tool,'') AS tool,
COALESCE(u.detail,'') AS command,COALESCE(r.output,'') AS output,COALESCE(r.is_error,false) AS is_error,u.created_at,r.matches
FROM filtered u LEFT JOIN LATERAL (
SELECT count(*) AS matches,min(detail) AS output,bool_or(is_error) AS is_error FROM activity r
WHERE r.kind='tool_result' AND r.tool_use_id=u.tool_use_id AND COALESCE(u.tool_use_id,'')<>''
AND r.exploration_id=u.exploration_id AND r.worker IS NOT DISTINCT FROM u.worker
AND r.node_id IS NOT DISTINCT FROM u.node_id AND r.main_seg IS NOT DISTINCT FROM u.main_seg) r ON true),
state AS (SELECT (SELECT blocked FROM archive_state) OR EXISTS(SELECT 1 FROM paired WHERE matches>1)
OR EXISTS(SELECT 1 FROM filtered u JOIN activity duplicate ON duplicate.kind='tool_use'
AND duplicate.id<>u.id AND duplicate.tool_use_id=u.tool_use_id AND COALESCE(u.tool_use_id,'')<>''
AND duplicate.exploration_id=u.exploration_id AND duplicate.worker IS NOT DISTINCT FROM u.worker
AND duplicate.node_id IS NOT DISTINCT FROM u.node_id AND duplicate.main_seg IS NOT DISTINCT FROM u.main_seg) AS blocked)
""",(self._scope.task_id,exp,q,'%'+q+'%')
    def list(self,q='',page=0,size=50):
        if type(page) is not int or type(size) is not int:raise ValueError('Integer page required')
        page=max(0,page);size=50 if size<=0 else size
        if size>self.max_rows or page*size>1000000:raise ValueError('Page exceeds bound')
        base,args=self._base(q)
        sql=base+""", page AS (SELECT * FROM paired ORDER BY id DESC LIMIT $5 OFFSET $6), bounded AS (
SELECT *,sum(octet_length(worker)+octet_length(tool)+octet_length(command)+octet_length(output)) OVER () AS bytes FROM page)
SELECT (SELECT count(*) FROM filtered),state.blocked,b.id,b.exploration_id,
"""+','.join('CASE WHEN b.bytes<=$7 AND NOT state.blocked THEN b.'+name+' ELSE NULL END' for name in ('worker','tool','command','output'))+""",b.is_error,b.created_at
FROM state LEFT JOIN bounded b ON NOT state.blocked ORDER BY b.id DESC"""
        with self._read('list',q) as tx:
            rows=tx.fetch_all(Query(sql,args+(size,page*size,self.max_bytes)))
            if not rows or len(rows)>size:raise CommandUnavailable('History result unavailable')
            total=_integer(rows[0][0]);out=[]
            for row in rows:
                count,blocked,id,exp,worker,tool,command,output,error,created=row
                if blocked is not False or count!=total:raise CommandUnavailable('Ambiguous or archived history unavailable')
                if id is None:continue
                if any(v is None for v in (worker,tool,command,output)) or type(error) is not bool:raise CommandUnavailable('History byte bound exceeded')
                out.append(dict(id=id,exploration_id=exp,worker=worker,tool=tool,command=command,output=output,is_error=error,created_at=created))
            return out,total
    def stats(self,q=''):
        base,args=self._base(q)
        sql=base+""", grouped AS (SELECT COALESCE(NULLIF(tool,''),'-') AS tool,count(*) AS total,count(*) FILTER (WHERE is_error) AS errors
FROM paired GROUP BY 1), bounded AS (SELECT *,sum(octet_length(tool)) OVER () AS bytes FROM grouped)
SELECT state.blocked,CASE WHEN bytes<=$5 AND NOT state.blocked THEN tool ELSE NULL END,total,errors
FROM state LEFT JOIN bounded ON NOT state.blocked ORDER BY total DESC,tool ASC LIMIT $6"""
        with self._read('stats',q) as tx:
            rows=tx.fetch_all(Query(sql,args+(self.max_bytes,self.max_rows+1)))
            if not rows or len(rows)>self.max_rows:raise CommandUnavailable('History stats exceed bound')
            out=[]
            for blocked,tool,total,errors in rows:
                if blocked is not False:raise CommandUnavailable('Ambiguous or archived history unavailable')
                if total is None:continue
                if tool is None:raise CommandUnavailable('Tool name byte bound exceeded')
                out.append(dict(tool=tool,total=_integer(total),errors=_integer(errors)))
            return out
