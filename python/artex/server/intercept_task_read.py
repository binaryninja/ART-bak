"""Standalone task-key intercept history read; no decisions or execution navigation."""
from dataclasses import dataclass
import re,json,math
from urllib.parse import urlsplit,parse_qsl,unquote
from ..db.intercept_history import InterceptHistoryStore,InspectionScope,InspectionDenied,InspectionUnavailable,_FIELDS
from ..db.intercept_detail_read import _time
from .core import json_response,error_response

@dataclass(frozen=True)
class TaskInterceptSelection:
    task_key:str
    unpaged:bool
    page:int
    size:int
    status:str
    decision_source:str

class InvalidTaskRequest(ValueError):pass

def parse_request(request):
    match=re.fullmatch(r'/api/intercept/task/([^/]+)',request.path)
    if request.method!='GET' or not match or request.body:raise InvalidTaskRequest()
    raw=match[1]
    if re.search(r'%(?![0-9a-fA-F]{2})',raw):raise InvalidTaskRequest()
    key=unquote(raw,errors='strict')
    if not key or len(key.encode())>256 or '/' in key or '\\' in key or any(ord(c)<32 or ord(c)==127 for c in key):raise InvalidTaskRequest()
    query=urlsplit(request.target).query
    if len(query)>2048:raise InvalidTaskRequest()
    pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=4,errors='strict')
    if len({k for k,v in pairs})!=len(pairs):raise InvalidTaskRequest()
    q=dict(pairs)
    if set(q)-{'page','size','status','decision_source'}:raise InvalidTaskRequest()
    status=q.get('status','');source=q.get('decision_source','')
    if status not in ('','pending','allowed','denied','timeout') or source not in ('','model','rule','unknown'):raise InvalidTaskRequest()
    unpaged=not q.get('page') and not q.get('size') and not status and not source
    def number(key,default):
        value=q.get(key,'')
        if not re.fullmatch('[+-]?[0-9]{1,19}',value):return default
        n=int(value)
        return n if -(2**63)<=n<2**63 else default
    page=max(1,number('page',1));size=number('size',20);size=min(100,20 if size<1 else size)
    if (page-1)*size>1000000:raise InvalidTaskRequest()
    return TaskInterceptSelection(key,unpaged,page,size,status,source)

class TaskInterceptReadService:
    def __init__(self,store,*,authorize,max_response_bytes=8388608):
        if type(store) is not InterceptHistoryStore or type(store.scope) is not InspectionScope or store.scope.task_key is None or not callable(authorize):raise ValueError('Exact task-scoped store and request grant required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Finite response bound required')
        self.store=store;self.authorize=authorize;self.max_bytes=max_response_bytes
        self._pins=(store,store.executor,store.scope,store.guard,store.authorize,store.raw,store.current,authorize,store.max_rows,store.max_bytes,max_response_bytes)
    def _check(self):
        s,e,scope,g,a,raw,c,request,rows,bound,response=self._pins
        if self.store is not s or s.executor is not e or s._executor is not e or s.scope is not scope or s._scope is not scope or s.guard is not g or s._guard is not g or s.authorize is not a or s.raw is not raw or s.current is not c or self.authorize is not request or type(s.max_rows) is not int or s.max_rows!=rows or type(s.max_bytes) is not int or s.max_bytes!=bound or self.max_bytes!=response:raise InspectionDenied()
    def handle(self,request):
        try:
            try:selection=parse_request(request)
            except (ValueError,UnicodeError):raise InvalidTaskRequest() from None
            s,e,scope,g,a,raw,c,request_grant,rows,bound,response_bound=self._pins
            if selection.task_key!=scope.task_key:raise InspectionDenied()
            kind='task_all' if selection.unpaged else 'history'
            source_selection=(kind,None,1 if selection.unpaged else selection.page,rows+1 if selection.unpaged else selection.size,selection.status,selection.decision_source)
            def current():
                self._check()
                if c(scope) is not True:raise InspectionDenied()
                self._check()
            def allow():
                current()
                if request_grant(request,scope,selection) is not True:raise InspectionDenied()
                current()
                if a(scope,source_selection) is not True:raise InspectionDenied()
                current()
                if raw(scope,source_selection) is not True:raise InspectionDenied()
                current()
                if request_grant(request,scope,selection) is not True or a(scope,source_selection) is not True:raise InspectionDenied()
                current()
            allow()
            # Store owns its guard. Do not wrap its read in another non-reentrant
            # guard; final disclosure checks occur after store guard exit too.
            values,total=s.read(kind,page=selection.page,size=selection.size,status=selection.status,decision_source=selection.decision_source)
            allow();items=[]
            for row in values:
                if type(row) is not dict or set(row)!=set(_FIELDS):raise InspectionUnavailable()
                item=dict(row)
                if type(item['id']) is not int or not 0<item['id']<2**63 or item['task_id']!=scope.task_key:raise InspectionUnavailable()
                for key in ('rule_id','conversation_id'):
                    if item[key] is not None and (type(item[key]) is not int or not -(2**63)<=item[key]<2**63):raise InspectionUnavailable()
                for key in ('agent_name','tool_name','status','reason','decision_source','conv_title','conv_agent_key','rule_name'):
                    if type(item[key]) is not str:raise InspectionUnavailable()
                    item[key].encode('utf-8')
                item['created_at']=_time(item['created_at'])
                if item['decided_at'] is not None:item['decided_at']=_time(item['decided_at'])
                # JSONB is decoded by the driver. Validate exact JSON primitives
                # before copying/serialization; no custom-object callbacks.
                pending=[(item['tool_input'],0)];count=0
                while pending:
                    value,depth=pending.pop();count+=1
                    if count>20000 or depth>64:raise InspectionUnavailable()
                    if type(value) is dict:
                        for key in value:
                            if type(key) is not str:raise InspectionUnavailable()
                            key.encode('utf-8')
                        pending.extend((v,depth+1) for v in value.values())
                    elif type(value) is list:pending.extend((v,depth+1) for v in value)
                    elif type(value) is str:value.encode('utf-8')
                    elif value is None or type(value) in (bool,int):pass
                    elif type(value) is float and math.isfinite(value):pass
                    else:raise InspectionUnavailable()
                if len(json.dumps(item['tool_input'],ensure_ascii=False,allow_nan=False).encode())>bound:raise InspectionUnavailable()
                items.append(item)
            result={'items':items,'total':len(items) if selection.unpaged else total}
            if not selection.unpaged:result.update(page=selection.page,page_size=selection.size)
            allow();response=json_response(200,result)
            if len(response.body)>response_bound:raise InspectionUnavailable()
            allow();return response
        except InspectionDenied:return error_response(403,'Task intercept read denied')
        except InvalidTaskRequest:return error_response(400,'Invalid task intercept request')
        except Exception:return error_response(500,'Task intercept history unavailable')
