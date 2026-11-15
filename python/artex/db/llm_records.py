"""Persisted debug records and metering, source db/commands.go and llm_usage.go.

Explicit recording_schema initialization required; no automatic DDL/enablement.
Bodies are exact TEXT, never parsed, normalized, truncated or silently dropped.
The optional fixed task scope applies to every insert/read/aggregate. Without it
this is an administrator/global store, not an authorization decision. Session
filter deliberately preserves source ILIKE wildcard semantics, not exact scope.
Count and page use separate snapshots as upstream. Size capped at 1000 and text
at 16MiB: explicit errors rather than truncated records. No implicit retries;
callers must not retry provider work after uncertain recording commits.
No delete, archive merging or global profile/daily totals pretending hot-only
rows are complete. SQL errors propagate; callers decide whether metering failure
should interrupt their runtime, never this persistence layer silently ignoring it.
"""
from dataclasses import dataclass,asdict
from .core import Query,int64
from .postgres import NoRowsError

class RecordInvalid(ValueError):pass
class RecordUnavailable(RuntimeError):pass


def _text(value):
    if not isinstance(value,str) or '\x00' in value:raise RecordInvalid('Record text required without NUL')
    try:encoded=value.encode('utf-8')
    except UnicodeError:raise RecordInvalid('Invalid record text') from None
    if len(encoded)>16777216:raise RecordInvalid('Record text exceeds bound')
    return value

def _integer(value):
    if type(value) is not int or not -(2**31)<=value<2**31:raise RecordInvalid('Record integer exceeds database range')
    return value

@dataclass
class LLMRecord:
    id:int=0
    ts:object=None
    model:str=''
    profile_name:str=''
    session_id:str=''
    task_id:str=''
    worker:str=''
    latency_ms:int=0
    input_tokens:int=0
    output_tokens:int=0
    cache_read:int=0
    cache_write:int=0
    status:str=''
    error:str=''
    request_body:str=''
    response_body:str=''
    raw_request:str=''
    raw_response:str=''
    def to_dict(self):
        result=asdict(self)
        if hasattr(self.ts,'isoformat'):result['ts']=self.ts.isoformat()
        for key in ('error','request_body','response_body','raw_request','raw_response'):
            if not result[key]:result.pop(key)
        return result

@dataclass
class LLMUsage:
    task_id:str=''
    exploration_id:int=0
    worker:str=''
    model:str=''
    profile_name:str=''
    latency_ms:int=0
    input_tokens:int=0
    output_tokens:int=0
    cache_read:int=0
    cache_write:int=0
    status:str=''

COLUMNS="id,ts,COALESCE(model,''),COALESCE(profile_name,''),COALESCE(session_id,''),COALESCE(task_id,''),COALESCE(worker,''),COALESCE(latency_ms,0),COALESCE(input_tokens,0),COALESCE(output_tokens,0),COALESCE(cache_read,0),COALESCE(cache_write,0),COALESCE(status,''),COALESCE(error,'')"

def _row(row):
    if len(row) not in (14,18):raise RecordUnavailable('Invalid recording row')
    values=list(row)
    if len(values)==18:values[14:]=[v or '' for v in values[14:]]
    return LLMRecord(*values)

class LLMRecordStore:
    def __init__(self,executor,*,task_id=None):
        self.executor=executor
        self.task_id=None if task_id is None else _text(task_id)
    def _scope(self,task,*,filter=False):
        task=_text(task)
        if self.task_id is not None:
            if task and task!=self.task_id:raise RecordInvalid('Task outside recording scope')
            if not filter and task!=self.task_id:raise RecordInvalid('Task outside recording scope')
            return self.task_id
        return task
    def insert(self,record):
        if not isinstance(record,LLMRecord):raise RecordInvalid('LLMRecord required')
        self._scope(record.task_id)
        names=('model','profile_name','session_id','task_id','worker','latency_ms','input_tokens','output_tokens','cache_read','cache_write','status','error','request_body','response_body','raw_request','raw_response')
        nullable={'profile_name','task_id','worker','error','request_body','response_body','raw_request','raw_response'}
        params=[]
        for n in names:
            v=_integer(getattr(record,n)) if n in ('latency_ms','input_tokens','output_tokens','cache_read','cache_write') else _text(getattr(record,n))
            params.append(None if n in nullable and v=='' else v)
        count=self.executor.execute(Query('INSERT INTO llm_records('+','.join(names)+') VALUES ('+','.join('$'+str(i) for i in range(1,17))+')',tuple(params)))
        if count!=1:raise RecordUnavailable('Record insertion not confirmed')
    def list(self,model='',session='',task='',page=0,size=50):
        model=_text(model);session=_text(session);task=self._scope(task,filter=True)
        if type(page) is not int or type(size) is not int:raise RecordInvalid('Integer page and size required')
        page=max(page,0);size=50 if size<=0 else size
        if size>1000 or page*size>=2**63:raise RecordInvalid('Recording page exceeds bound')
        terms=['true'];params=[]
        for term,value,enabled in [('model =',model,bool(model)),('session_id ILIKE','%'+session+'%',bool(session)),("COALESCE(task_id,'') =",task,bool(task) or self.task_id is not None)]:
            if enabled:params.append(value);terms.append(term+' $'+str(len(params)))
        where=' WHERE '+' AND '.join(terms)
        total=self.executor.fetch_one(Query('SELECT COUNT(*) FROM llm_records'+where,tuple(params)))[0]
        n=len(params)
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM llm_records'+where+f' ORDER BY id DESC LIMIT ${n+1} OFFSET ${n+2}',tuple(params)+(size,page*size)))
        return [_row(row) for row in rows],total
    def get(self,id):
        int64(id,'record ID')
        if id<=0:raise RecordInvalid('Positive record ID required')
        sql='SELECT '+COLUMNS+',request_body,response_body,raw_request,raw_response FROM llm_records WHERE id=$1';params=(id,)
        if self.task_id is not None:sql+=" AND COALESCE(task_id,'')=$2";params+=(self.task_id,)
        try:row=self.executor.fetch_one(Query(sql,params))
        except NoRowsError:return None
        return None if row is None else _row(row)
    def tasks(self):
        sql="SELECT task_id,COUNT(*) FROM llm_records WHERE COALESCE(task_id,'')<>''";params=()
        if self.task_id is not None:sql+=' AND task_id=$1';params=(self.task_id,)
        rows=self.executor.fetch_all(Query(sql+' GROUP BY task_id ORDER BY MAX(id) DESC',params))
        return [dict(task_id=row[0],count=row[1]) for row in rows]
    def insert_usage(self,usage):
        if not isinstance(usage,LLMUsage):raise RecordInvalid('LLMUsage required')
        self._scope(usage.task_id);int64(usage.exploration_id,'exploration ID')
        params=(_text(usage.task_id),usage.exploration_id if usage.exploration_id>0 else None,*(_text(getattr(usage,n)) for n in ('worker','model','profile_name')),*(_integer(getattr(usage,n)) for n in ('latency_ms','input_tokens','output_tokens','cache_read','cache_write')),_text(usage.status))
        count=self.executor.execute(Query('''INSERT INTO llm_usage(task_id,exploration_id,worker,model,profile_name,latency_ms,input_tokens,output_tokens,cache_read,cache_write,status)
VALUES (NULLIF($1,''),$2,NULLIF($3,''),NULLIF($4,''),NULLIF($5,''),$6,$7,$8,$9,$10,$11)''',params))
        if count!=1:raise RecordUnavailable('Usage insertion not confirmed')
    def token_by_model(self,task):
        task=self._scope(task)
        rows=self.executor.fetch_all(Query("""SELECT COALESCE(NULLIF(model,''),'(unknown)') AS model,COUNT(*) AS calls,
COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(cache_read),0),COALESCE(SUM(cache_write),0)
FROM llm_usage WHERE COALESCE(task_id,'')=$1 GROUP BY model ORDER BY SUM(input_tokens)+SUM(output_tokens) DESC,model""",(task,)))
        return [dict(zip(('model','calls','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens'),row)) for row in rows]
