"""Read-only persisted intercept requests, not live waiter/approval authority.

Source db/intercept.go uses TEXT task_id without a task FK. An explicit task_key
is matched verbatim; host current/guard must bind its generation and workspace.
No numeric-task inference, decisions, audit/execution projection or archive reads.
History defaults here are bounded pages; pending fails rather than truncating.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from .core import Query,int64
class InspectionDenied(PermissionError):pass
class InspectionUnavailable(RuntimeError):pass
@dataclass(frozen=True)
class InspectionScope:
    workspace:str
    task_key:str|None=None

_FIELDS=('id','rule_id','conversation_id','task_id','agent_name','tool_name','tool_input','status','reason','decided_at','created_at','decision_source','conv_title','conv_agent_key','rule_name')
_TEXT=('task_id','agent_name','tool_name','tool_input','status','reason','decision_source','conv_title','conv_agent_key','rule_name')
_SOURCE="COALESCE(NULLIF(ip.decision_source,''),CASE WHEN ip.rule_id IS NOT NULL THEN 'rule' WHEN ip.reason LIKE U&'[\\6a21\\578b]%' THEN 'model' ELSE 'unknown' END)"
class InterceptHistoryStore:
    def __init__(self,executor,scope,*,authorize,raw_authorize,current,lifecycle_guard,max_rows=1000,max_bytes=1048576):
        if type(scope) is not InspectionScope or not isinstance(scope.workspace,str) or not 1<=len(scope.workspace)<=256:raise ValueError('Explicit scope required')
        if scope.task_key is not None and (not isinstance(scope.task_key,str) or not 1<=len(scope.task_key.encode())<=256 or '\0' in scope.task_key):raise ValueError('Exact task key required')
        if any(not callable(v) for v in (authorize,raw_authorize,current,lifecycle_guard)):raise ValueError('Explicit read authority required')
        if type(max_rows) is not int or not 1<=max_rows<=10000 or type(max_bytes) is not int or not 1024<=max_bytes<=16777216:raise ValueError('Invalid bounds')
        self.executor,self.scope,self.guard=executor,scope,lifecycle_guard
        self._executor,self._scope,self._guard=executor,scope,lifecycle_guard
        self.authorize,self.raw,self.current=authorize,raw_authorize,current
        self.max_rows,self.max_bytes=max_rows,max_bytes
    def _check(self):
        def pins():
            if self.executor is not self._executor or self.scope is not self._scope or self.guard is not self._guard:raise InspectionDenied()
        pins()
        if self.current(self._scope) is not True:raise InspectionDenied()
        pins()
    def validate_read(self,selection):
        """Fresh output-time grants under the same host guard; no SQL/effects."""
        with self._guard(self._scope):
            self._check()
            if self.authorize(self._scope,selection) is not True:raise InspectionDenied()
            self._check()
            if self.raw(self._scope,selection) is not True:raise InspectionDenied()
            self._check()
    def read(self,kind,*,id=None,page=1,size=20,status='',decision_source=''):
        policies=(self.authorize,self.raw,self.current,)
        def check():
            if any(a is not b for a,b in zip((self.authorize,self.raw,self.current,),policies)):raise InspectionDenied()
            self._check()
            if any(a is not b for a,b in zip((self.authorize,self.raw,self.current,),policies)):raise InspectionDenied()
        if kind not in ('pending','one','history'):raise ValueError('Unsupported projection')
        if status not in ('','pending','allowed','denied','timeout') or decision_source not in ('','model','rule','unknown'):raise ValueError('Invalid filter')
        if type(page) is not int or type(size) is not int:raise ValueError('Integer page required')
        page=max(1,page);size=min(100,20 if size<=0 else size)
        if kind=='one':
            if int64(id,'request ID')<=0:raise ValueError('Positive ID required')
            size=1
        elif id is not None:raise ValueError('Unexpected ID')
        if kind=='pending':status='pending';size=self.max_rows+1;page=1
        if size>self.max_rows and kind!='pending' or (page-1)*size>1000000:raise ValueError('Page exceeds bound')
        selection=(kind,id,page,size,status,decision_source)
        sql="""WITH filtered AS (SELECT ip.id,ip.rule_id,ip.conversation_id,ip.task_id,ip.agent_name,ip.tool_name,
ip.tool_input,ip.status,ip.reason,ip.decided_at,ip.created_at,"""+(_SOURCE if kind=='history' else 'ip.decision_source')+""" AS decision_source,
COALESCE(c.title,'') AS conv_title,COALESCE(c.agent_key,'') AS conv_agent_key,COALESCE(ir.name,'') AS rule_name
FROM intercept_pending ip LEFT JOIN conversations c ON c.id=ip.conversation_id LEFT JOIN intercept_rules ir ON ir.id=ip.rule_id
WHERE ($1::text IS NULL OR ip.task_id=$1) AND ($2::bigint IS NULL OR ip.id=$2)
AND ($3='' OR ip.status=$3) AND ($4='' OR """+_SOURCE+"""=$4)), page AS
(SELECT * FROM filtered ORDER BY created_at DESC,id DESC LIMIT $5 OFFSET $6), bounded AS
(SELECT *,sum("""+'+'.join('COALESCE(octet_length('+f+'::text),0)' for f in _TEXT)+""") OVER () AS bytes FROM page)
SELECT (SELECT count(*) FROM filtered),"""+','.join(('CASE WHEN bytes<=$7 THEN b.'+f+' ELSE NULL END') if f in _TEXT else 'b.'+f for f in _FIELDS)+" FROM (SELECT 1) sentinel LEFT JOIN bounded b ON true ORDER BY b.created_at DESC,b.id DESC"
        with self._guard(self._scope):
            check()
            if self.authorize(self._scope,selection) is not True:raise InspectionDenied()
            check()
            if self.raw(self._scope,selection) is not True:raise InspectionDenied()
            check()
            rows=self._executor.fetch_all(Query(sql,(self._scope.task_key,id,status,decision_source,size,(page-1)*size,self.max_bytes)))
            check()
            if self.authorize(self._scope,selection) is not True:raise InspectionDenied()
            check()
            if self.raw(self._scope,selection) is not True:raise InspectionDenied()
            check()
            if not rows or len(rows)>min(size,self.max_rows):raise InspectionUnavailable()
            total=rows[0][0]
            if type(total) is not int or total<0:raise InspectionUnavailable()
            out=[]
            for count,*values in rows:
                if count!=total:raise InspectionUnavailable()
                if values[0] is None:continue
                row=dict(zip(_FIELDS,values))
                if any(row[f] is None for f in _TEXT if f!='task_id'):raise InspectionUnavailable()
                if self._scope.task_key is not None and row['task_id']!=self._scope.task_key:raise InspectionUnavailable()
                if kind!='history':
                    for f in ('conv_title','conv_agent_key','rule_name'):row.pop(f)
                out.append(row)
            return out,total
