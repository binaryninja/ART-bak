"""Bounded recorded intercept detail, never reconstructed execution or approval."""
import json,re,math
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .core import Query
from .intercept_history import InspectionScope,_FIELDS,_TEXT,_SOURCE

class DetailUnavailable(RuntimeError):pass

def _time(value):
    if type(value) is not datetime or type(value.tzinfo) not in (timezone,ZoneInfo):raise DetailUnavailable()
    text=value.isoformat()
    if len(text)>40 or value.utcoffset() is None:raise DetailUnavailable()
    return re.sub(r'(\.\d*?[1-9])0+(?=[+-]|$)',r'\1',text).replace('+00:00','Z')

def _json(raw):
    if type(raw) is not str:raise DetailUnavailable()
    value=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(DetailUnavailable()))
    pending=[(value,0)];count=0
    while pending:
        item,depth=pending.pop();count+=1
        if depth>64 or count>20000:raise DetailUnavailable()
        if type(item) is dict:
            for key in item:key.encode('utf-8')
            pending.extend((v,depth+1) for v in item.values())
        elif type(item) is list:pending.extend((v,depth+1) for v in item)
        elif type(item) is str:item.encode('utf-8')
        elif type(item) is float and not math.isfinite(item):raise DetailUnavailable()
    return value


def _struct_fields(value):
    # Reject ambiguous aliases rather than allow later values to hide an invalid
    # earlier typed field. Raw model_input objects never pass through this helper.
    result={}
    for key,item in value.items():
        folded=key.casefold()
        if folded in result:raise DetailUnavailable()
        result[folded]=item
    return result

def _audit(raw):
    if raw is None:return None
    source=_json(raw)
    if source is None:return None
    if type(source) is not dict:raise DetailUnavailable()
    # Go struct decoding accepts case-insensitive JSON field spellings. Only
    # structural audit/context objects are folded; raw model_input stays intact.
    source=_struct_fields(source)
    required=('correlation','input_digest','user_message','initial_action','initial_reason','execution_status')
    optional=('run_id','tool_use_id','model_input_digest','effective_action','decision_reason','rule_name','config_digest','output')
    bools=('user_truncated','context_truncated','model_fallback','output_truncated')
    out={}
    # Go's struct decoder ignores unknown fields and maps null strings to zero.
    for key in required+optional:
        value=source.get(key)
        if value is None:value=''
        if type(value) is not str:raise DetailUnavailable()
        if key in required or value:out[key]=value
    for key in bools:
        value=source.get(key,False)
        if value is None:value=False
        if type(value) is not bool:raise DetailUnavailable()
        if value:out[key]=True
    profile=source.get('profile_id',0)
    if profile is None:profile=0
    if type(profile) is not int or not -(2**63)<=profile<2**63:raise DetailUnavailable()
    if profile:out['profile_id']=profile
    for key in ('captured_at','execution_ended_at'):
        value=source.get(key)
        if value is None:
            if key=='captured_at':out[key]='0001-01-01T00:00:00Z'
            continue
        if type(value) is not str or len(value)>40 or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)',value):raise DetailUnavailable()
        datetime.fromisoformat(value.replace('Z','+00:00'))
        out[key]=value
    context=source.get('context')
    if context is not None:
        if type(context) is not list or len(context)>2000:raise DetailUnavailable()
        entries=[]
        for entry in context:
            if entry is None:entry={}
            if type(entry) is not dict:raise DetailUnavailable()
            entry=_struct_fields(entry)
            item={}
            for key in ('kind','tool','tool_use_id','text'):
                value=entry.get(key)
                if value is None:value=''
                if type(value) is not str:raise DetailUnavailable()
                if key in ('kind','text') or value:item[key]=value
            for key in ('is_error','truncated'):
                value=entry.get(key,False)
                if value is None:value=False
                if type(value) is not bool:raise DetailUnavailable()
                if value:item[key]=True
            entries.append(item)
        context=entries
    out['context']=context
    if 'model_input' in source:out['model_input']=source['model_input']
    return out

class InterceptDetailReader:
    def __init__(self,executor,scope,*,max_bytes=1048576):
        if not callable(getattr(executor,'fetch_all',None)) or type(scope) is not InspectionScope:raise ValueError('Explicit scoped executor required')
        if type(scope.workspace) is not str or not 1<=len(scope.workspace.encode())<=256:raise ValueError('Workspace required')
        if scope.task_key is not None and (type(scope.task_key) is not str or not 1<=len(scope.task_key.encode())<=256 or '\0' in scope.task_key):raise ValueError('Exact task key required')
        if type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Finite bytes required')
        self.executor=executor;self.scope=scope;self.max_bytes=max_bytes;self._pins=(executor,scope,max_bytes)
    def validate(self):
        e,s,b=self._pins
        if self.executor is not e or self.scope is not s or type(self.max_bytes) is not int or self.max_bytes!=b:raise DetailUnavailable()
    def read(self,id):
        self.validate()
        if type(id) is not int or not 0<id<2**63:raise ValueError('Positive record ID required')
        fields=(*_FIELDS,'audit')
        text=(*_TEXT,'audit')
        columns="ip.id,ip.rule_id,ip.conversation_id,ip.task_id,ip.agent_name,ip.tool_name,ip.tool_input::text AS tool_input,ip.status,ip.reason,ip.decided_at,ip.created_at,"+_SOURCE+" AS decision_source,COALESCE(c.title,'') AS conv_title,COALESCE(c.agent_key,'') AS conv_agent_key,COALESCE(ir.name,'') AS rule_name,ip.audit::text AS audit"
        size='+'.join('COALESCE(octet_length('+f+'),0)' for f in text)
        projected=','.join('CASE WHEN bytes<=$3 THEN '+f+' ELSE NULL END AS '+f if f in text else f for f in fields)
        sql='WITH selected AS (SELECT '+columns+' FROM intercept_pending ip LEFT JOIN conversations c ON c.id=ip.conversation_id LEFT JOIN intercept_rules ir ON ir.id=ip.rule_id WHERE ip.id=$1 AND ($2::text IS NULL OR ip.task_id=$2)), bounded AS (SELECT *,('+size+') AS bytes FROM selected) SELECT bytes<=$3 AS within_bound,'+projected+' FROM bounded'
        rows=self._pins[0].fetch_all(Query(sql,(id,self.scope.task_key,self.max_bytes)));self.validate()
        if type(rows) not in (list,tuple) or len(rows)>1:raise DetailUnavailable()
        if not rows:return None
        row=rows[0]
        if type(row) not in (list,tuple) or len(row)!=17 or row[0] is not True:raise DetailUnavailable()
        out=dict(zip(fields,row[1:]));used=0
        for key in text:
            value=out[key]
            if value is None and key in ('task_id','audit'):continue
            if type(value) is not str:raise DetailUnavailable()
            used+=len(value.encode())
        if used>self.max_bytes:raise DetailUnavailable()
        if type(out['id']) is not int or out['id']!=id:raise DetailUnavailable()
        for key in ('rule_id','conversation_id'):
            if out[key] is not None and (type(out[key]) is not int or not -(2**63)<=out[key]<2**63):raise DetailUnavailable()
        if self.scope.task_key is not None and out['task_id']!=self.scope.task_key:raise DetailUnavailable()
        out['created_at']=_time(out['created_at'])
        if out['decided_at'] is not None:out['decided_at']=_time(out['decided_at'])
        out['tool_input']=_json(out['tool_input']);out['audit']=_audit(out['audit'])
        return out
