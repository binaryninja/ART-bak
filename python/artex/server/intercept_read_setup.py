"""Explicit optional JWT/global-authorized intercept metadata view.

No original routes are changed. Only two protected GET declarations are staged;
other paths use the original app. Intercept paths never fall through to mutation
handlers. Existing strict services reject HEAD/query/body rather than widening
that contract. No listener, schema, cache, matcher or execution permission.

Successful bytes are fenced after service serialization and after start_response.
If that last callback revokes authority, transmission aborts without a body; the
already-emitted status cannot be replaced with a new HTTP response. Policies
must remain read-only/cooperative; these finite callback fences do not establish
an atomic snapshot across arbitrary mutually mutating policy callbacks.
"""
from dataclasses import dataclass,field
from .auth import AuthService
from .core import Router,error_response
from .wsgi import WSGIApplication
from .intercept_config_read import InterceptConfigReader,InterceptConfigReadService
from .tool_intercept_read import ToolInterceptReadService
from ..db.tool_intercept_read import ToolInterceptReader

_PATHS={'/api/intercept/tool-config':'config','/api/intercept/rules':'rules'}

def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)

def _same(method,key):return all(a is b for a,b in zip(_key(method),key))

@dataclass(frozen=True)
class InterceptReadBundle:
    application:object=field(repr=False)
    config:InterceptConfigReadService=field(repr=False)
    rules:ToolInterceptReadService=field(repr=False)

class _InterceptReadApplication:
    def __init__(self,application,config,rules,authorize_global):
        self.original=application;self.config=config;self.rules=rules;self.authorize_global=authorize_global
        base=application.router;auth=base.authorize;owner=auth.__self__
        self._pins=(application,base,auth,owner,authorize_global,config,rules,config.reader.executor,application.max_body_bytes)
        self._auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
        self._methods=(_key(application.__call__),_key(application._handle),_key(base.dispatch),_key(auth))
        self._services={kind:(service,_key(service.handle),_key(service._check),service.reader,_key(service.reader.read),_key(service.reader.validate)) for kind,service in (('config',config),('rules',rules))}
        self._base_routes=tuple(base._routes)
        staged=Router(auth)
        staged.add('GET','/api/intercept/tool-config',lambda r,p:self._handle(r,'config'))
        staged.add('GET','/api/intercept/rules',lambda r,p:self._handle(r,'rules'))
        self._router=staged;self._routes=tuple(staged._routes)
    def _check(self):
        app,base,auth,owner,grant,config,rules,executor,bound=self._pins
        if (self.original is not app or app.router is not base or base.authorize is not auth
            or self.config is not config or self.rules is not rules or self.authorize_global is not grant
            or app.max_body_bytes!=bound or tuple(base._routes)!=self._base_routes
            or self._router.authorize is not auth or tuple(self._router._routes)!=self._routes):raise RuntimeError('Read binding changed')
        if any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),self._auth_pins)):
            raise RuntimeError('Original authentication changed')
        if any(not _same(method,key) for method,key in zip((app.__call__,app._handle,base.dispatch,owner.authorized),self._methods)):
            raise RuntimeError('Original transport changed')
        for service,handle,check,reader,read,validate in self._services.values():
            if service.reader is not reader or reader.executor is not executor or not _same(service.handle,handle) or not _same(service._check,check) or not _same(reader.read,read) or not _same(reader.validate,validate):raise RuntimeError('Read service changed')
            service._check()
    def _disclose(self,request,kind):
        self._check();auth=self._pins[2];service=self._services[kind][0]
        if auth(request) is not True:raise PermissionError()
        self._check()
        if self._pins[4](request,kind) is not True:raise PermissionError()
        self._check()
        if service._pins[3]() is not True:raise PermissionError()
        self._check()
        if service._pins[2](request) is not True:raise PermissionError()
        self._check()
        if auth(request) is not True:raise PermissionError()
        self._check()
        # Authentication may consult a revocable external clock/policy. Refresh
        # disclosure grants AFTER its last callback, then mirror the services'
        # current-after-grant fence. Finish with pure resource identity checks,
        # not another outer authentication callback that can stale these grants.
        if self._pins[4](request,kind) is not True:raise PermissionError()
        self._check()
        if service._pins[3]() is not True:raise PermissionError()
        self._check()
        if service._pins[2](request) is not True:raise PermissionError()
        self._check()
        if service._pins[3]() is not True:raise PermissionError()
        self._check()
    def _handle(self,request,kind):
        try:
            self._disclose(request,kind)
            result=self._services[kind][0].handle(request)
            if result.status==200:self._disclose(request,kind)
            else:self._check()
            return result
        except PermissionError:return error_response(403,'Intercept metadata access denied')
        except Exception:return error_response(503,'Intercept metadata unavailable')
    def __call__(self,environ,start_response):
        path=environ.get('PATH_INFO','')
        if path not in _PATHS:return self.original(environ,start_response)
        state={};outer=self
        class Dispatch:
            def dispatch(self,request):
                state['request']=request
                try:outer._check()
                except Exception:return error_response(503,'Intercept metadata unavailable')
                return outer._router.dispatch(request)
        transport=WSGIApplication(Dispatch(),max_body_bytes=self._pins[8])
        def capture(status,headers,exc_info=None):state.update(status=status,headers=headers,exc_info=exc_info)
        body=transport(environ,capture)
        successful=state['status'].startswith('200 ')
        if successful:
            try:self._disclose(state['request'],_PATHS[path])
            except Exception:
                response=error_response(403,'Intercept metadata access denied')
                body=[response.body];state.update(status='403 Forbidden',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')])
                successful=False
        start_response(state['status'],state['headers'])
        if successful:
            try:self._disclose(state['request'],_PATHS[path])
            except Exception:raise RuntimeError('Intercept disclosure revoked after response start') from None
        return body

def bind_intercept_reads(application,*,config_service,rules_service,authorize_global):
    """Create an optional view, never install routes into the original app.

    Existing reader grants remain mandatory; authorize_global(request, kind)
    separately permits disclosure of global config or raw rules metadata.
    """
    if type(application) is not WSGIApplication or type(application.router) is not Router:
        raise ValueError('Exact original authenticated application and router required')
    base=application.router;auth=base.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:
        raise ValueError('Original AuthService JWT authorizer required')
    if type(config_service) is not InterceptConfigReadService or type(rules_service) is not ToolInterceptReadService or not callable(authorize_global):
        raise ValueError('Exact read services and explicit global disclosure grant required')
    if type(config_service.reader) is not InterceptConfigReader or type(rules_service.reader) is not ToolInterceptReader or config_service.reader.executor is not rules_service.reader.executor:
        raise ValueError('Exact original readers sharing one executor required')
    # Pure structural conflict checks precede service checks and all callbacks.
    for method,path,regex,handler,public in base._routes:
        if method in ('GET','HEAD') and any(regex.fullmatch(target) for target in _PATHS):
            raise ValueError('Intercept read route conflicts with original application')
    for service in (config_service,rules_service):
        reader=service.reader
        if not _same(service.handle,(service,type(service).handle)) or not _same(service._check,(service,type(service)._check)) or not _same(reader.read,(reader,type(reader).read)) or not _same(reader.validate,(reader,type(reader).validate)):
            raise ValueError('Original read service methods required')
    config_service._check();rules_service._check()
    view=_InterceptReadApplication(application,config_service,rules_service,authorize_global)
    view._check()
    return InterceptReadBundle(view,config_service,rules_service)
