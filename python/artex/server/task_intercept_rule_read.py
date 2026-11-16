"""Optional task rule metadata only; no mutations, global catalog or enforcement."""
from urllib.parse import urlsplit
from .core import json_response,error_response
from .disclosure import DisclosureResponse
from .intercept_rules import _dto
from ..db.intercept_rules import InterceptRuleStore,RuleScope,RuleDenied,RuleUnavailable

class TaskInterceptRuleRead:
    def __init__(self,executor,scope,*,workspace,authorize,request_authorize,current,lifecycle_guard,max_rows=500,max_bytes=1048576):
        from .task_runtime_reads import TaskReadScope
        if type(scope) is not TaskReadScope:raise ValueError('Exact selected scope required')
        if type(workspace) is not str or not 1<=len(workspace.encode())<=256:raise ValueError('Explicit workspace required')
        if any(not callable(f) for f in (authorize,request_authorize,current,lifecycle_guard)):raise ValueError('Independent rule authority required')
        if type(max_rows) is not int or not 1<=max_rows<=500 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Bounded rule read required')
        self._pins=(executor,scope,workspace,authorize,request_authorize,current,lifecycle_guard,max_rows,max_bytes)
        self._original=self._pins;self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def handle(self,request,*,transport_disclosure,transport_authentication):
        executor,scope,workspace,source,http,current,guard,rows,maximum=self._original
        def pure():
            if self._pins is not self._original or (scope.task_id,scope.exploration_id,scope.revision)!=self._identity:raise RuleDenied()
        def check():
            pure()
            if current(scope) is not True:raise RuleDenied()
            pure();return True
        def authority():
            pure()
            if http(request,scope) is not True:raise RuleDenied()
            pure()
            if source(scope) is not True:raise RuleDenied()
            pure();return True
        def receipt():
            authority();transport_disclosure()
            with guard(scope):check()
            check();authority();transport_authentication();pure()
        try:
            if not callable(transport_disclosure) or not callable(transport_authentication):raise ValueError()
            if request.method.upper() not in ('GET','HEAD') or request.body or urlsplit(request.target).query or request.path!=f'/api/tasks/{scope.task_id}/intercept-rules':raise ValueError()
            receipt()
            # The existing store owns its guard and transaction. No extra nested
            # external guard is acquired around its list operation.
            store=InterceptRuleStore(executor,RuleScope(workspace,scope.task_id,scope.exploration_id),
                authorize=lambda s,op:authority(),current=lambda s:check(),
                lifecycle_guard=lambda s:guard(scope),max_rows=rows,max_bytes=maximum)
            result={'rules':[_dto(row) for row in store.list()]}
            response=json_response(200,result)
            if len(response.body)>maximum:raise RuleUnavailable('Response bound')
            receipt()
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except (PermissionError,RuleDenied):return error_response(403,'Task rule read denied')
        except ValueError:return error_response(400,'Invalid task rule request')
        except Exception:return error_response(503,'Task rule read unavailable')
