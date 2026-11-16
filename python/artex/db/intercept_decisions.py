"""Explicit scoped interception persistence; no execution, decisions UI or defaults.

Ports CreateInterceptPending/CreateDecidedIntercept and ResolveIntercept. Scope
is one canonical numeric task+exploration OR one conversation, never global.
Legacy opaque task TEXT reads belong to the separate inspection APIs. Audit JSON
is supplied evidence, never reconstructed run/tool-use correlation. Unknown writes
quarantine this store; no automatic retry or outcome adoption.
"""
from dataclasses import dataclass,fields
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import json
from threading import RLock
from .core import Query
from .intercept_detail_read import _audit,_json

class DecisionDenied(PermissionError):pass
class DecisionConflict(RuntimeError):pass
class DecisionUnavailable(RuntimeError):pass
class DecisionUncertain(RuntimeError):pass

def _text(v,limit,nonempty=False):
    if type(v) is not str or '\0' in v or len(v.encode('utf-8'))>limit or (nonempty and not v):raise ValueError('Invalid bounded text')
def _id(v):
    if type(v) is not int or not 0<v<2**63:raise ValueError('Positive identity required')
def _encoded(v,audit=False):
    if v is None and audit:return None
    _text(v,262144)
    if not v and not audit:v='{}'
    # Strict duplicate rejection avoids silently changing supplied audit evidence.
    def pairs(items):
        out={}
        for k,x in items:
            if k in out:raise ValueError('Duplicate JSON field')
            out[k]=x
        return out
    json.loads(v,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Invalid JSON')))
    _json(v) # finite scalars, UTF8, depth and node caps
    if audit:
        parsed=_audit(v)
        if parsed is None:raise ValueError('Audit object or SQL null required')
    return v
@dataclass(frozen=True)
class DecisionScope:
    workspace:str
    revision:str
    task_id:int|None=None
    exploration_id:int|None=None
    conversation_id:int|None=None
    def validate(self):
        if type(self) is not DecisionScope:raise ValueError('Exact scope required')
        _text(self.workspace,256,True);_text(self.revision,256,True)
        if self.task_id is not None:
            _id(self.task_id);_id(self.exploration_id)
            if self.conversation_id is not None:raise ValueError('One parent required')
        else:
            if self.exploration_id is not None:raise ValueError('One parent required')
            _id(self.conversation_id)
@dataclass(frozen=True)
class DecisionRecord:
    tool_name:str
    input_json:str='{}'
    reason:str=''
    agent_name:str=''
    rule_id:int|None=None
    audit_json:str|None=None
    status:str='pending'
    def validate(self):
        if type(self) is not DecisionRecord:raise ValueError('Exact record required')
        _text(self.tool_name,1024,True);_text(self.reason,65536);_text(self.agent_name,1024)
        if self.rule_id is not None:_id(self.rule_id)
        if type(self.status) is not str or self.status not in ('pending','allowed','denied'):raise ValueError('Invalid initial status')
        _encoded(self.input_json);_encoded(self.audit_json,True)
@dataclass(frozen=True)
class DecisionResolution:
    record_id:int
    status:str
    action:str
    reason:str
    def validate(self):
        if type(self) is not DecisionResolution:raise ValueError('Exact resolution required')
        _id(self.record_id);_text(self.reason,65536)
        if type(self.status) is not str or self.status not in ('allowed','denied','timeout') or type(self.action) is not str or self.action not in ('allow','deny'):raise ValueError('Invalid resolution')
        if self.status in ('allowed','denied') and self.action!=('allow' if self.status=='allowed' else 'deny'):raise ValueError('Inconsistent resolution')

def _snapshot(value):return tuple((f.name,type(getattr(value,f.name)),getattr(value,f.name)) for f in fields(value))
_FIELDS=('id','rule_id','conversation_id','task_id','agent_name','tool_name','tool_input','status','reason','decided_at','created_at','decision_source','audit')
class InterceptDecisionStore:
    def __init__(self,executor,scope,*,read_authorize,write_authorize,current,lifecycle_guard,max_bytes=1048576):
        if type(scope) is not DecisionScope:raise ValueError("Exact scope required")
        scope.validate()
        if any(not callable(v) for v in (read_authorize,write_authorize,current,lifecycle_guard)):raise ValueError('Explicit policies required')
        if type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Bound required')
        self.executor=executor;self.scope=scope;self.read_authorize=read_authorize;self.write_authorize=write_authorize;self.current=current;self.guard=lifecycle_guard;self.max_bytes=max_bytes
        self._pins=(executor,scope,read_authorize,write_authorize,current,lifecycle_guard,max_bytes,executor.transaction)
        self._scope_values=_snapshot(scope);self.unresolved=False;self._active=False;self._lock=RLock()
    def _pure(self):
        e,s,r,w,c,g,b,t=self._pins
        if self.unresolved:raise DecisionUncertain('Persistence requires reconciliation')
        if self.executor is not e or self.scope is not s or self.read_authorize is not r or self.write_authorize is not w or self.current is not c or self.guard is not g or type(self.max_bytes) is not int or self.max_bytes!=b or e.transaction!=t or _snapshot(s)!=self._scope_values:raise DecisionDenied('Binding changed')
        s.validate()
    def _allow(self,operation,selection,identity,request_authorize):
        def pure():
            self._pure()
            if (selection if operation=="read" else _snapshot(selection))!=identity:raise DecisionDenied("Selection changed")
        pure()
        if self._pins[4](self._pins[1]) is not True:raise DecisionDenied('Lifetime revoked')
        pure()
        grant=self._pins[2] if operation=='read' else self._pins[3]
        if grant(self._pins[1],operation,selection) is not True:raise DecisionDenied('Operation denied')
        pure()
        if self._pins[4](self._pins[1]) is not True:raise DecisionDenied('Lifetime revoked')
        pure()
        # Final lifetime callback can revoke operation authority. Refresh the
        # original operation grant before the optional outer request fence.
        if grant(self._pins[1],operation,selection) is not True:raise DecisionDenied('Operation revoked')
        pure()
        if request_authorize is not None and request_authorize(self._pins[1],operation,selection) is not True:raise DecisionDenied('Request denied')
        pure()
        actual=selection if operation=='read' else _snapshot(selection)
        if actual!=identity:raise DecisionDenied('Selection changed')
        if operation!='read':selection.validate()
    def _parent(self,tx):
        s=self._pins[1]
        if s.task_id is not None:
            q=Query('SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM task_archives a WHERE a.task_id=tasks.id) FOR SHARE',(s.task_id,s.exploration_id));expected=s.task_id
        else:q=Query('SELECT id FROM conversations WHERE id=$1 FOR SHARE',(s.conversation_id,));expected=s.conversation_id
        rows=tx.fetch_all(q)
        if type(rows) not in (list,tuple) or len(rows)!=1 or tuple(rows[0])!=(expected,) or type(rows[0][0]) is not int:raise DecisionDenied('Parent unavailable')
    def _scope_sql(self):
        s=self._pins[1]
        return ('task_id=$2 AND conversation_id IS NULL',str(s.task_id)) if s.task_id is not None else ('conversation_id=$2 AND task_id IS NULL',s.conversation_id)
    def _operation(self,op,selection,action,request_authorize):
        with self._lock:return self._operation_locked(op,selection,action,request_authorize)
    def _operation_locked(self,op,selection,action,request_authorize):
        identity=selection if op=='read' else _snapshot(selection);attempted=False
        if request_authorize is not None and not callable(request_authorize):raise ValueError('Invalid request grant')
        def allow():self._allow(op,selection,identity,request_authorize)
        def attempt():
            nonlocal attempted
            attempted=True
        self._pure()
        if self._active:raise DecisionDenied('Concurrent operation unsupported')
        self._active=True
        try:
            with self._pins[5](self._pins[1]):
                allow()
                with self._pins[0].transaction() as tx:
                    self._parent(tx);allow();result=action(tx,allow,attempt);allow()
                allow()
            allow()
            return result
        except BaseException as exc:
            if attempted:
                self.unresolved=True
                raise DecisionUncertain('Persistence outcome unknown; do not retry') from None
            if isinstance(exc,Exception) and not isinstance(exc,(DecisionDenied,DecisionConflict,DecisionUnavailable,DecisionUncertain)):raise DecisionUnavailable('Persistence unavailable') from None
            raise
        finally:self._active=False
    def create(self,record,*,request_authorize=None):
        if type(record) is not DecisionRecord:raise ValueError("Exact record required")
        record.validate()
        def action(tx,allow,attempt):
            s=self._pins[1]
            source='rule' if record.rule_id is not None else 'model' if record.reason.startswith('[模型]') else 'unknown'
            params=(record.rule_id,s.conversation_id,str(s.task_id) if s.task_id is not None else None,record.agent_name,record.tool_name,_encoded(record.input_json),record.status,record.reason,source,record.audit_json)
            if record.rule_id is not None:
                rows=tx.fetch_all(Query('SELECT id FROM intercept_rules WHERE id=$1 FOR SHARE',(record.rule_id,)))
                if len(rows)!=1 or tuple(rows[0])!=(record.rule_id,):raise DecisionConflict('Rule unavailable')
            allow();attempt()
            rows=tx.fetch_all(Query("INSERT INTO intercept_pending(rule_id,conversation_id,task_id,agent_name,tool_name,tool_input,status,reason,decided_at,decision_source,audit) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,CASE WHEN $7='pending' THEN NULL ELSE now() END,$9,$10::jsonb) RETURNING id",params))
            if len(rows)!=1 or len(rows[0])!=1:raise DecisionUnavailable('Invalid insert result')
            _id(rows[0][0]);return rows[0][0]
        return self._operation('create',record,action,request_authorize)
    def resolve(self,resolution,*,request_authorize=None):
        if type(resolution) is not DecisionResolution:raise ValueError("Exact resolution required")
        resolution.validate()
        def action(tx,allow,attempt):
            clause,parent=self._scope_sql()
            patch=json.dumps({'effective_action':resolution.action,'decision_reason':resolution.reason,'execution_status':'awaiting_result' if resolution.action=='allow' else 'not_executed'},ensure_ascii=True,separators=(',',':'))
            allow();attempt()
            n=tx.execute(Query("UPDATE intercept_pending SET status=$3,decided_at=now(),audit=CASE WHEN audit IS NULL THEN NULL ELSE audit || $4::jsonb || CASE WHEN $5='allow' AND audit->>'correlation' IS DISTINCT FROM 'exact' THEN '{\"execution_status\":\"unknown\"}'::jsonb ELSE '{}'::jsonb END END WHERE id=$1 AND "+clause+" AND status='pending'",(resolution.record_id,parent,resolution.status,patch,resolution.action)))
            if type(n) is not int or n not in (0,1):raise DecisionUnavailable('Invalid resolution result')
            return n==1
        return self._operation('resolve',resolution,action,request_authorize)
    def get(self,record_id,*,request_authorize=None):
        _id(record_id)
        def action(tx,allow,attempt):
            clause,parent=self._scope_sql()
            # Gate all secret-bearing columns on total bytes before transfer.
            sizes="octet_length(COALESCE(task_id,''))+octet_length(agent_name)+octet_length(tool_name)+octet_length(tool_input::text)+octet_length(reason)+octet_length(decision_source)+octet_length(COALESCE(audit::text,''))+octet_length(status)"
            columns=[]
            for k in _FIELDS:
                expression=k+'::text' if k in ('tool_input','audit') else k
                columns.append('CASE WHEN bytes<=$3 THEN '+expression+' ELSE NULL END AS '+k if k in ('task_id','agent_name','tool_name','tool_input','status','reason','decision_source','audit') else k)
            q=Query('WITH bounded AS (SELECT *,('+sizes+') AS bytes FROM intercept_pending WHERE id=$1 AND '+clause+' LIMIT 1) SELECT '+','.join(columns)+',bytes FROM bounded',(record_id,parent,self._pins[6]))
            rows=tx.fetch_all(q);allow()
            if not rows:return None
            if len(rows)!=1 or len(rows[0])!=14 or type(rows[0][-1]) is not int or not 0<=rows[0][-1]<=self._pins[6]:raise DecisionUnavailable('Record bounds exceeded')
            d=dict(zip(_FIELDS,rows[0][:-1]));s=self._pins[1]
            if type(d['id']) is not int or d['id']!=record_id or d['task_id']!=(str(s.task_id) if s.task_id is not None else None) or d['conversation_id']!=s.conversation_id:raise DecisionUnavailable('Record scope mismatch')
            if d['rule_id'] is not None:_id(d['rule_id'])
            if d['conversation_id'] is not None:_id(d['conversation_id'])
            for k in ('agent_name','tool_name','status','reason','decision_source'):_text(d[k],self._pins[6])
            if d['status'] not in ('pending','allowed','denied','timeout') or d['decision_source'] not in ('','rule','model','unknown'):raise DecisionUnavailable('Record state unsupported')
            d['tool_input']=_json(d['tool_input']);d['audit']=_audit(d['audit'])
            for k in ('created_at','decided_at'):
                v=d[k]
                if v is None and k=='decided_at':continue
                if type(v) is not datetime or type(v.tzinfo) not in (timezone,ZoneInfo):raise DecisionUnavailable('Invalid timestamp')
            return d
        return self._operation('read',record_id,action,request_authorize)
