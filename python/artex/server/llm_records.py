"""Protected recording inspector reads, grounded in server/commands.go.

Only exact source routes: records list/detail/tasks/by-model; tokens/usage remains
503 (archive-aware totals are not implemented). DELETE remains503 unless an
explicit delete_authorize policy is supplied. It
deletes only exact-task debug records, not metering/archive data. No invented
capture/export endpoint or provider execution. Detail includes normalized/raw
capture fields ONLY through explicit per-record authorization AND redaction.

All reads require authorize(request,operation,selection)->literal True and an
explicit store_resolver(request,operation,selection) returning a scoped store.
A None task means the host is explicitly authorizing an unfiltered selection,
not implicit fallback. session is source ILIKE substring search, NOT a security
boundary or conversation authorization. Use a fixed task scope from the resolver
and per-record policy for conversation ownership; no conv-to-task guess is made.
Selection includes resolved store task scope for a second policy check. Stored
errors/body text may contain secrets: redact(request,record_dict,include_bodies)
is mandatory for row outputs. It may remove optional fields but cannot change
identity or add fields. No generic sanitizer can promise to remove every secret.
Metadata/usage failures are500, not fabricated zero results. Deletion additionally
requires delete_authorize(request, "delete", selection)
returning literal True, both before resolution and with the resolved scope. This
policy must verify action-specific authorization for permanent deletion, not infer
it from JWT/read rights. No default deletion policy is installed.
"""
from copy import deepcopy
from dataclasses import dataclass,replace,asdict
import re
from urllib.parse import urlsplit,parse_qs
from .core import error_response,json_response
from ..db.llm_records import LLMRecordStore,LLMRecord,RecordInvalid


@dataclass(frozen=True)
class RecordSelection:
    task:str|None=None
    session:str=''
    model:str=''
    page:int=0
    size:int=50
    record_id:int|None=None
    store_task:str|None=None
    record_task:str|None=None
    record_session:str|None=None


class RecordDenied(RuntimeError):pass
class RecordUnavailable(RuntimeError):pass


def _text(value):
    if not isinstance(value,str) or not value or len(value)>4096 or '\0' in value:raise RecordInvalid('Invalid selector')
    value.encode('utf-8')
    return value


def _number(value,minimum,maximum):
    if not re.fullmatch(r'[0-9]{1,19}',value):raise RecordInvalid('Invalid number')
    result=int(value)
    if not minimum<=result<=maximum:raise RecordInvalid('Out of range')
    return result


class LLMRecordService:
    def __init__(self,store_resolver=None,*,authorize=None,redact=None,delete_authorize=None):
        if delete_authorize is not None and not callable(delete_authorize):
            raise ValueError('Explicit delete authorization callback required')
        self.resolve,self.authorize,self.redact=store_resolver,authorize,redact
        self.delete_authorize=delete_authorize
    def register(self,router):
        for method,path,action in [('GET','/api/llm/records','list'),('GET','/api/llm/records/tasks','tasks'),
            ('GET','/api/llm/records/by-model','models'),('GET','/api/llm/records/{id}','detail'),
            ('DELETE','/api/llm/records','delete'),('GET','/api/tokens/usage','unavailable')]:
            router.add(method,path,lambda req,params,a=action:self.handle(a,req,params))
    def _allow(self,request,action,selection):
        if not callable(self.authorize):raise RecordUnavailable()
        if self.authorize(request,action,selection) is not True:raise RecordDenied()
    def _record(self,request,record,selection,*,body):
        if not isinstance(record,LLMRecord):raise RecordUnavailable()
        scoped=replace(selection,record_id=record.id,record_task=record.task_id,record_session=record.session_id)
        if selection.store_task is not None and record.task_id!=selection.store_task:raise RecordDenied()
        if selection.task is not None and record.task_id!=selection.task:raise RecordDenied()
        self._allow(request,'record_body' if body else 'record_metadata',scoped)
        if not callable(self.redact):raise RecordUnavailable()
        original=record.to_dict()
        if not body:
            for name in ('request_body','response_body','raw_request','raw_response'):original.pop(name,None)
        sanitized=self.redact(request,deepcopy(original),body)
        if not isinstance(sanitized,dict) or set(sanitized)-set(original):raise RecordUnavailable()
        for name in ('id','task_id','session_id'):
            if sanitized.get(name)!=original.get(name):raise RecordUnavailable()
        return deepcopy(sanitized)
    def _delete(self,request):
        if not callable(self.delete_authorize):raise RecordUnavailable()
        if request.body:raise RecordInvalid('Deletion body unsupported')
        query=parse_qs(urlsplit(request.target).query,keep_blank_values=True)
        if not query:return error_response(400,'missing task')
        if set(query)!={'task'} or len(query['task'])!=1:raise RecordInvalid('Exact task required')
        task=query['task'][0].strip()
        if not task: return error_response(400,'missing task')
        task=_text(task)
        if len(task.encode('utf-8'))>4096:raise RecordInvalid('Task exceeds byte bound')
        selection=RecordSelection(task=task)
        def allowed(selected):
            self._allow(request,'delete',selected)
            try:approved=self.delete_authorize(request,'delete',selected)
            except Exception:raise RecordUnavailable() from None
            if approved is not True:raise RecordDenied()
        allowed(selection)
        if not callable(self.resolve):raise RecordUnavailable()
        store=self.resolve(request,'delete',selection)
        if not isinstance(store,LLMRecordStore):raise RecordUnavailable()
        if store.task_id is not None and store.task_id!=task:raise RecordDenied()
        allowed(replace(selection,store_task=store.task_id))
        count=store.delete_task(task)
        return json_response(200,{'deleted':count})

    def handle(self,action,request,params):
        try:
            if action=='unavailable':raise RecordUnavailable()
            if action=='delete':return self._delete(request)
            query=parse_qs(urlsplit(request.target).query,keep_blank_values=True)
            allowed={'model','session','task','page','size'} if action=='list' else {'task'} if action=='models' else set()
            if set(query)-allowed or any(len(v)!=1 for v in query.values()):raise RecordInvalid('Invalid query')
            value={k:v[0] for k,v in query.items()}
            task=_text(value['task']) if 'task' in value else None
            if action=='models' and task is None:raise RecordInvalid('Task required')
            selection=RecordSelection(task=task,session=_text(value['session']) if 'session' in value else '',
                model=_text(value['model']) if 'model' in value else '',
                page=_number(value.get('page','0'),0,2**31-1),size=_number(value.get('size','50'),1,1000),
                record_id=_number(params['id'],1,2**63-1) if 'id' in params else None)
            self._allow(request,action,selection)
            if not callable(self.resolve):raise RecordUnavailable()
            store=self.resolve(request,action,selection)
            if not isinstance(store,LLMRecordStore):raise RecordUnavailable()
            scoped=replace(selection,store_task=store.task_id)
            if task is not None and store.task_id is not None and task!=store.task_id:raise RecordDenied()
            self._allow(request,action,scoped)
            if action=='list':
                if not callable(self.redact):raise RecordUnavailable()
                records,total=store.list(model=selection.model,session=selection.session,task=task or '',page=selection.page,size=selection.size)
                return json_response(200,{'records':[self._record(request,r,scoped,body=False) for r in records],'total':total})
            if action=='detail':
                if not callable(self.redact):raise RecordUnavailable()
                record=store.get(selection.record_id)
                if record is None:return error_response(404,'Record not found')
                return json_response(200,self._record(request,record,scoped,body=True))
            if action=='tasks':return json_response(200,{'tasks':store.tasks()})
            return json_response(200,{'models':store.token_by_model(task)})
        except RecordDenied:return error_response(403,'Recording access denied')
        except RecordUnavailable:return error_response(503,'Recording capability unavailable')
        except (ValueError,UnicodeError):return error_response(400,'Invalid recording request')
        except Exception:return error_response(500,'Recording operation failed')
