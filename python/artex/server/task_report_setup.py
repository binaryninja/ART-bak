"""Explicit fixed-scope report overlay on an existing original JWT WSGI app.

No resolver/default route, active task fallback or extra global permission.
After start_response, revoked disclosure aborts bytes; it cannot replace headers.
Finite cooperative grant fences are not an atomic arbitrary-callback snapshot.
"""
from dataclasses import dataclass
from contextvars import ContextVar
from .core import Router,error_response
from .wsgi import WSGIApplication
from .auth import AuthService
from .task_report import TaskReportService,PreparedReport
from ..db.task_report import TaskReportStore

_STATE=ContextVar('fixed_report_request',default=None)
_AUTH=AuthService.authorized
_SERVICE_METHODS=tuple(getattr(TaskReportService,n) for n in ('_check','prepare','validate_disclosure'))
_STORE_METHODS=tuple(getattr(TaskReportStore,n) for n in ('check','read','validate_read'))

@dataclass(frozen=True)
class TaskReportReadBundle:
    application:object
    service:TaskReportService

class _ReportApplication:
    def __init__(self,application,service):
        self.original=application;self.service=service
        base=application.router;auth=base.authorize;owner=auth.__self__
        self._pins=(application,base,auth,owner,service,service.store,service.store._pins,application.max_body_bytes)
        self._auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
        self._base_routes=tuple(base._routes)
        self._base_methods=(application.__call__,application._handle,base.dispatch)
        staged=Router(auth)
        staged.add('GET','/api/report',lambda req,params:self._handle(req))
        self._router=staged
        self._routes=tuple(self._router._routes)
    def _check(self):
        app,base,auth,owner,service,store,storepins,bound=self._pins
        if (self.original is not app or self.service is not service or app.router is not base or base.authorize is not auth
            or app.max_body_bytes!=bound or tuple(base._routes)!=self._base_routes
            or (app.__call__,app._handle,base.dispatch)!=self._base_methods
            or self._router.authorize is not auth or tuple(self._router._routes)!=self._routes
            or service.store is not store or store._pins is not storepins
            or AuthService.authorized is not _AUTH or getattr(owner.authorized,'__func__',None) is not _AUTH
            or any(x is not y for x,y in zip((owner.store,owner.codec,owner.key,owner.clock),self._auth_pins))):raise PermissionError('Original report composition changed')
        for obj,names,methods in ((service,('_check','prepare','validate_disclosure'),_SERVICE_METHODS),(store,('check','read','validate_read'),_STORE_METHODS)):
            for name,method in zip(names,methods):
                if getattr(type(obj),name) is not method or getattr(getattr(obj,name),'__func__',None) is not method:raise PermissionError('Original report methods required')
        service._check()
    def _disclose(self,state):
        self._check()
        receipt=state.get('prepared')
        if type(receipt) is not PreparedReport:raise PermissionError('Original prepared report required')
        self.service.validate_disclosure(receipt)
        self._check()
    def _handle(self,request):
        try:
            self._check();state=_STATE.get()
            if state is None or state.get('request') is not request:raise PermissionError()
            receipt=self.service.prepare(request,jwt_authorize=self._pins[2])
            if type(receipt) is not PreparedReport:raise PermissionError()
            state['prepared']=receipt
            self._disclose(state)
            return receipt.response
        except Exception:return error_response(403,'Report access denied')
    def __call__(self,environ,start_response):
        if environ.get('PATH_INFO')!='/api/report':return self.original(environ,start_response)
        state={};outer=self
        class Dispatch:
            def dispatch(self,request):
                state['request']=request
                try:outer._check()
                except Exception:return error_response(403,'Report access denied')
                return outer._router.dispatch(request)
        transport=WSGIApplication(Dispatch(),max_body_bytes=self._pins[7])
        def capture(status,headers):state.update(status=status,headers=headers)
        token=_STATE.set(state)
        try:body=transport(environ,capture)
        finally:_STATE.reset(token)
        successful=state['status'].startswith('200 ')
        if successful:
            try:self._disclose(state)
            except Exception:
                response=error_response(403,'Report access denied');body=[response.body]
                state.update(status='403 Forbidden',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')]);successful=False
        start_response(state['status'],state['headers'])
        if successful:
            try:self._disclose(state)
            except Exception:raise RuntimeError('Report disclosure revoked after response start') from None
        return body

def build_task_report_application(application,service):
    """No callbacks, reads or mutation of the original app during composition."""
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(service) is not TaskReportService or type(service.store) is not TaskReportStore:
        raise ValueError('Exact original JWT app and fixed report service required')
    auth=application.router.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not _AUTH or AuthService.authorized is not _AUTH:
        raise ValueError('Original AuthService JWT authorization required')
    if any(method in ('GET','HEAD') and regex.fullmatch('/api/report') for method,path,regex,handler,public in application.router._routes):raise ValueError('Report route already registered')
    wrapper=_ReportApplication(application,service);wrapper._check()
    return TaskReportReadBundle(wrapper,service)
