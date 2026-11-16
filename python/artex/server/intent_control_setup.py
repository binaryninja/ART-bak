"""Optional protected intent route; installs one original host adapter explicitly."""
from dataclasses import dataclass
import re
from .core import Router,error_response,json_response
from .wsgi import WSGIApplication
from .auth import AuthService
from .intent_control import IntentControlService,_CONTEXT

_AUTH=AuthService.authorized
@dataclass(frozen=True)
class IntentControlBundle:
    application:object
    service:IntentControlService

class _Application:
    def __init__(self,app,service):
        self.original=app;self.service=service
        auth=app.router.authorize;owner=auth.__self__
        self._pins=(app,app.router,auth,owner,owner.store,owner.codec,owner.key,owner.clock,service,app.max_body_bytes)
        self._routes=tuple(app.router._routes)
        staged=Router(auth)
        staged.add('POST','/api/tasks/{id}/intents/{iid}/control',self._handle)
        self._router=staged;self._staged=tuple(staged._routes)
    def check(self):
        app,router,auth,owner,store,codec,key,clock,service,limit=self._pins
        if self.original is not app or self.service is not service or app.router is not router or router.authorize is not auth or tuple(router._routes)!=self._routes or app.max_body_bytes!=limit or self._router.authorize is not auth or tuple(self._router._routes)!=self._staged:raise PermissionError()
        if AuthService.authorized is not _AUTH or getattr(owner.authorized,'__func__',None) is not _AUTH or any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),(store,codec,key,clock))):raise PermissionError()
        for name,method in _WRAPPER_METHODS.items():
            if getattr(type(self),name) is not method or getattr(getattr(self,name),'__func__',None) is not method:raise PermissionError('Original composition methods required')
        service.check()
    def inspect(self,request,task_id,intent_id):
        # Explicit in-process reconciliation only; no invented HTTP route.
        from .intent_control import IntentSelector
        if type(task_id) is not str or not re.fullmatch('[1-9][0-9]{0,18}',task_id) or int(task_id)>=2**63 or type(intent_id) is not int or not 0<intent_id<2**63:raise ValueError('Exact selector required')
        if self.service.fixed_task_id is not None and task_id!=self.service.fixed_task_id:raise PermissionError()
        state={'service':self.service,'binding':self.check,'jwt':self._pins[2],'request':request,
               'request_values':(request.method,request.target,request.body,tuple(request.headers.items())),
               'headers':request.headers,'selector':IntentSelector(task_id,intent_id,'pause'),'selector_values':(task_id,intent_id,'pause')}
        state['selector_original']=state['selector']
        token=_CONTEXT.set(state)
        try:
            self.service._request_grant(state)
            result=self.service.host.inspect_intent_control(task_id,intent_id,timeout=self.service.timeout)
            self._disclose(state)
            return result
        finally:_CONTEXT.reset(token)
    def _disclose(self,state):
        self.service._request_grant(state)
        proposal=state.get('proposal')
        if proposal is not None and self.service._authorize(proposal) is not True:raise PermissionError('Original proposal grant revoked')
        self.service._request_pins(state)
    def _handle(self,request,params):return self.service.handle(request,params,_CONTEXT.get())
    def __call__(self,environ,start_response):
        if not re.fullmatch('/api/tasks/[^/]+/intents/[^/]+/control',environ.get('PATH_INFO','')):return self.original(environ,start_response)
        state={'service':self.service,'binding':self.check,'jwt':self._pins[2]};outer=self
        class Dispatch:
            def dispatch(self,request):
                state.update(request=request,request_values=(request.method,request.target,request.body,tuple(request.headers.items())),headers=request.headers)
                try:outer.check()
                except Exception:return error_response(403,'Intent control binding unavailable')
                return outer._router.dispatch(request)
        def capture(status,headers):state.update(status=status,response_headers=headers)
        token=_CONTEXT.set(state)
        try:
            body=WSGIApplication(Dispatch(),max_body_bytes=self._pins[9])(environ,capture)
            if state.get('selector') is not None:
                try:self._disclose(state)
                except Exception:
                    state['denied']=True
                    response=json_response(409 if state.get('attempted') else 403,{'error':'Intent authorization revoked','unknown':bool(state.get('attempted')),'retry':False})
                    body=[response.body];state.update(status='409 Conflict' if state.get('attempted') else '403 Forbidden',response_headers=list(response.headers.items())+[('Content-Length',str(len(response.body)))])
            start_response(state['status'],state['response_headers'])
            if state.get('selector') is not None and not state.get('denied'):self._disclose(state)
            return body
        finally:_CONTEXT.reset(token)

def build_intent_control_application(application,service):
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(service) is not IntentControlService:raise ValueError('Exact app and intent service required')
    auth=application.router.authorize
    if type(getattr(auth,'__self__',None)) is not AuthService or getattr(auth,'__func__',None) is not _AUTH:raise ValueError('Original JWT required')
    shape=lambda p:re.sub(r'\{[^}]+\}','{}',p)
    target='/api/tasks/{}/intents/{}/control'
    if any(m=='POST' and (shape(p)==target or regex.fullmatch('/api/tasks/5/intents/3/control')) for m,p,regex,h,public in application.router._routes):raise ValueError('Intent route conflict')
    wrapper=_Application(application,service);wrapper.check()
    from ..agent.engine_runner import EngineRunner
    runner=service.host.runner
    if type(runner) is not EngineRunner or getattr(runner.install_intent_control,'__func__',None) is not EngineRunner.install_intent_control:raise ValueError('Original control installer required')
    EngineRunner.install_intent_control(runner,service.adapter)
    return IntentControlBundle(wrapper,service)

_WRAPPER_METHODS={n:getattr(_Application,n) for n in ('check','inspect','_disclose','_handle','__call__')}
