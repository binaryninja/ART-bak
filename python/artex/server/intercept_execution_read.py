"""Read-only exact call/result navigation, not tool execution or approval."""
import re
from urllib.parse import urlsplit,parse_qsl
from ..db.intercept_execution_read import InterceptExecutionReader
from .core import json_response,error_response

class InvalidExecutionRequest(ValueError):pass

def parse_request(request):
    m=re.fullmatch(r'/api/intercept/history/([1-9][0-9]{0,18})/execution',request.path)
    if request.method!='GET' or not m or request.body:raise InvalidExecutionRequest()
    id=int(m[1])
    if id>=2**63:raise InvalidExecutionRequest()
    query=urlsplit(request.target).query
    if len(query)>256:raise InvalidExecutionRequest()
    pairs=parse_qsl(query,keep_blank_values=True,max_num_fields=1,errors='strict')
    if any(k!='conversation' for k,v in pairs):raise InvalidExecutionRequest()
    value=dict(pairs).get('conversation','');hint=None
    if re.fullmatch('[+]?[0-9]{1,19}',value):
        n=int(value)
        if 0<n<2**63:hint=n
    return id,hint

class InterceptExecutionReadService:
    def __init__(self,reader,*,authorize,raw_authorize,parent_authorize,current,lifecycle_guard,max_response_bytes=8388608):
        if type(reader) is not InterceptExecutionReader or any(not callable(p) for p in (authorize,raw_authorize,parent_authorize,current,lifecycle_guard)):raise ValueError('Exact reader and independent approval/raw/parent grants required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=16777216:raise ValueError('Finite response bound required')
        reader.validate();self.reader=reader;self.authorize=authorize;self.raw_authorize=raw_authorize;self.parent_authorize=parent_authorize;self.current=current;self.guard=lifecycle_guard;self.max_bytes=max_response_bytes
        self._pins=(reader,reader.detail,reader.executor,reader.scope,authorize,raw_authorize,parent_authorize,current,lifecycle_guard,max_response_bytes)
    def _check(self):
        r,d,e,s,a,raw,p,c,g,b=self._pins
        if self.reader is not r or r.detail is not d or r.executor is not e or r.scope is not s or self.authorize is not a or self.raw_authorize is not raw or self.parent_authorize is not p or self.current is not c or self.guard is not g or type(self.max_bytes) is not int or self.max_bytes!=b:raise PermissionError()
        r.validate()
    def handle(self,request):
        try:
            try:id,hint=parse_request(request)
            except (ValueError,UnicodeError):raise InvalidExecutionRequest() from None
            r,d,e,scope,a,raw,p,c,g,bound=self._pins;original=(request.method,request.target,request.body)
            def check():
                self._check()
                if original!=(request.method,request.target,request.body) or c(scope) is not True:raise PermissionError()
                self._check()
                if original!=(request.method,request.target,request.body):raise PermissionError()
            def allow():
                check()
                if a(request,scope,id,hint) is not True:raise PermissionError()
                check()
                if raw(request,scope,id,hint) is not True:raise PermissionError()
                check()
                if a(request,scope,id,hint) is not True:raise PermissionError()
                check()
            def parent_grant(parent):
                saved=tuple((type(v),v) for v in (parent.kind,parent.id,parent.task_key,parent.exploration_id,parent.tool_use_id))
                allow()
                if p(request,scope,id,parent) is not True:raise PermissionError()
                allow()
                if saved!=tuple((type(v),v) for v in (parent.kind,parent.id,parent.task_key,parent.exploration_id,parent.tool_use_id)):raise PermissionError()
                return True
            def disclose(result):
                allow()
                for parent in result.parents:parent_grant(parent)
                allow()
            allow()
            with g(scope):
                allow();result=r.read(id,hint,authorize_parent=parent_grant);disclose(result)
                response=json_response(result.status,result.body)
                if len(response.body)>bound:raise RuntimeError('Navigation response exceeds bound')
                # Fresh read validates original approval correlation, live parent
                # and exact paired rows after serialization, never retargets the
                # response when identity/content changed.
                fresh=r.read(id,hint,authorize_parent=parent_grant)
                if fresh!=result:raise PermissionError()
                disclose(result)
            # Guard exit is another boundary. Validate again before final grants.
            fresh=r.read(id,hint,authorize_parent=parent_grant)
            if fresh!=result:raise PermissionError()
            disclose(result);return response
        except InvalidExecutionRequest:return error_response(400,'Invalid intercept execution selector')
        except PermissionError:return error_response(403,'Intercept navigation read denied')
        except Exception:return error_response(500,'Intercept navigation unavailable')
