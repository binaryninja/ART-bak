"""Dynamic explicit task report composition; separately granted global counts."""
from .core import error_response
from .disclosure import DisclosureResponse
from .task_report import TaskReportService
from ..db.task_report import TaskReportStore,ReportScope

class TaskReportRead:
    def __init__(self,executor,scope,*,authorize,request_authorize,global_assets_authorize,current,lifecycle_guard):
        from .task_runtime_reads import TaskReadScope
        if type(scope) is not TaskReadScope or any(not callable(f) for f in (authorize,request_authorize,global_assets_authorize,current,lifecycle_guard)):
            raise ValueError('Exact report scope and independent grants required')
        self._pins=(executor,scope,authorize,request_authorize,global_assets_authorize,current,lifecycle_guard)
        self._original=self._pins;self._identity=(scope.task_id,scope.exploration_id,scope.revision)
    def handle(self,request,*,transport_disclosure,transport_authentication):
        e,s,source,http,global_assets,current,guard=self._original
        request_headers=request.headers
        request_identity=(request.method,request.target,request.body,tuple(request_headers.items()))
        def pure():
            if (type(s.task_id) is not int or type(s.exploration_id) is not int or type(s.revision) is not str
                or self._pins is not self._original or (s.task_id,s.exploration_id,s.revision)!=self._identity
                or request.headers is not request_headers
                or (request.method,request.target,request.body,tuple(request.headers.items()))!=request_identity):raise PermissionError()
        def authority():
            pure()
            if http(request,s) is not True or source(s) is not True or global_assets(s,ASSET_TYPES) is not True:raise PermissionError()
            pure()
        from ..db.task_report import ASSET_TYPES
        try:
            if not callable(transport_disclosure) or not callable(transport_authentication):raise ValueError()
            authority();transport_disclosure();authority()
            rs=ReportScope(*self._identity)
            store=TaskReportStore(e,rs,current=lambda other:current(s),lifecycle_guard=lambda other:guard(s),
                authorize=lambda other:source(s),global_assets_authorize=lambda other,kinds:global_assets(s,kinds))
            service=TaskReportService(store,authorize=lambda req,other:http(req,s))
            prepared=service.prepare(request)
            authority();transport_authentication();authority();pure()
            if prepared.response.status!=200:return prepared.response
            def receipt():
                pure();authority();transport_disclosure()
                service.validate_disclosure(prepared)
                with guard(s):
                    if current(s) is not True:raise PermissionError()
                if current(s) is not True:raise PermissionError()
                authority();transport_authentication();authority();pure()
            receipt();response=prepared.response
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except PermissionError:return error_response(403,'Report read access denied')
        except (ValueError,UnicodeError):return error_response(400,'Invalid report request')
        except Exception:return error_response(503,'Report read unavailable')
