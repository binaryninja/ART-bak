"""Opt-in persisted task approval history; no live decisions or execution rights."""
from dataclasses import replace
from .core import error_response
from .disclosure import DisclosureResponse
from .intercept_task_read import TaskInterceptReadService,parse_request
from ..db.intercept_history import InterceptHistoryStore,InspectionScope,InspectionDenied

class TaskInterceptHistoryRead:
    def __init__(self,executor,scope,*,workspace,authorize,raw_authorize,request_authorize,current,lifecycle_guard,max_rows=500,max_bytes=1048576):
        from .task_runtime_reads import TaskReadScope
        if type(scope) is not TaskReadScope:raise ValueError('Exact resolved task required')
        if type(workspace) is not str or not 1<=len(workspace.encode())<=256:raise ValueError('Explicit workspace required')
        if any(not callable(f) for f in (authorize,raw_authorize,request_authorize,current,lifecycle_guard)):raise ValueError('Independent metadata/raw/request grants required')
        if type(max_rows) is not int or not 1<=max_rows<=500 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Bounded history required')
        self._pins=(executor,scope,workspace,authorize,raw_authorize,request_authorize,current,lifecycle_guard,max_rows,max_bytes)
        self._original=self._pins;self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def handle(self,request,*,transport_disclosure,transport_authentication):
        e,s,workspace,source,raw,http,current,guard,rows,maximum=self._original
        def pure():
            if self._pins is not self._original or (s.task_id,s.exploration_id,s.revision)!=self._identity:raise InspectionDenied()
        def check():
            pure()
            if current(s) is not True:raise InspectionDenied()
            pure();return True
        try:
            if not callable(transport_disclosure) or not callable(transport_authentication):raise ValueError()
            # HEAD shares the GET selection but keeps original request authority.
            parsed_request=replace(request,method='GET') if request.method=='HEAD' else request
            selection=parse_request(parsed_request)
            if selection.task_key!=str(s.task_id):raise ValueError()
            kind='task_all' if selection.unpaged else 'history'
            source_selection=(kind,None,1 if selection.unpaged else selection.page,rows+1 if selection.unpaged else selection.size,selection.status,selection.decision_source)
            def authority():
                pure()
                if http(request,s,selection) is not True:raise InspectionDenied()
                pure()
                if source(s,source_selection) is not True:raise InspectionDenied()
                pure()
                if raw(s,source_selection) is not True:raise InspectionDenied()
                pure();return True
            def receipt():
                authority();transport_disclosure()
                with guard(s):check()
                check();authority();transport_authentication();pure()
            receipt()
            store=InterceptHistoryStore(e,InspectionScope(workspace,str(s.task_id)),
                authorize=lambda scope,sel:authority(),raw_authorize=lambda scope,sel:authority(),
                current=lambda scope:check(),lifecycle_guard=lambda scope:guard(s),max_rows=rows,max_bytes=maximum)
            service=TaskInterceptReadService(store,authorize=lambda req,scope,sel:authority(),max_response_bytes=maximum)
            response=service.handle(parsed_request,selection=selection)
            receipt()
            if response.status!=200:return response
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except InspectionDenied:return error_response(403,'Task intercept history denied')
        except (ValueError,UnicodeError):return error_response(400,'Invalid task intercept history request')
        except Exception:return error_response(503,'Task intercept history unavailable')
