"""Explicit optional JWT/global-authorized intercept metadata view.

No original routes are changed. Only explicitly supplied protected GET reads are staged;
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
from contextvars import ContextVar
from .auth import AuthService
from .core import Router,error_response
from .wsgi import WSGIApplication
from .intercept_config_read import InterceptConfigReader,InterceptConfigReadService
from .tool_intercept_read import ToolInterceptReadService
from ..db.tool_intercept_read import ToolInterceptReader

from . import judge_usage_read as _judge
from . import notification_history as _history
from ..db.judge_usage_read import JudgeUsageReader
from ..db.notification_history import NotificationHistoryReader,DeliverySelection

_PATHS={'/api/intercept/tool-config':'config','/api/intercept/rules':'rules',
        '/api/intercept/judge/usage':'judge_usage','/api/notify/deliveries':'notification_history'}
_PARSERS=(_judge.parse_request,_history.parse_request)
_REQUEST_STATE=ContextVar('metadata_read_request_state',default=None)
_TYPES={'config':(InterceptConfigReadService,InterceptConfigReader),
        'rules':(ToolInterceptReadService,ToolInterceptReader),
        'judge_usage':(_judge.JudgeUsageReadService,JudgeUsageReader),
        'notification_history':(_history.NotificationHistoryReadService,NotificationHistoryReader)}

def _selection_identity(kind,value):
    if kind=='judge_usage':
        if type(value) is not int:raise RuntimeError('Exact normalized days required')
        return (int,value)
    if kind=='notification_history':
        if type(value) is not DeliverySelection:raise RuntimeError('Exact delivery selection required')
        return (DeliverySelection,value.channel_id,value.state,value.event_kind,value.page,value.page_size)
    if value is not None:raise RuntimeError('Unexpected selection')
    return (None,)


def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)

def _same(method,key):return all(a is b for a,b in zip(_key(method),key))

@dataclass(frozen=True)
class InterceptReadBundle:
    application:object=field(repr=False)
    config:InterceptConfigReadService=field(repr=False)
    rules:ToolInterceptReadService=field(repr=False)

@dataclass(frozen=True)
class MetadataReadBundle:
    application:object=field(repr=False)
    config:object=None
    rules:object=None
    judge_usage:object=None
    notification_history:object=None

class _InterceptReadApplication:
    def __init__(self,application,services,authorize_global):
        config=services.get('config');rules=services.get('rules')
        self.original=application;self.config=config;self.rules=rules;self.authorize_global=authorize_global
        base=application.router;auth=base.authorize;owner=auth.__self__
        self._pins=(application,base,auth,owner,authorize_global,config,rules,next(iter(services.values())).reader.executor,application.max_body_bytes)
        self._auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
        self._methods=(_key(application.__call__),_key(application._handle),_key(base.dispatch),_key(auth))
        self._services={kind:(service,_key(service.handle),_key(service._check),service.reader,_key(service.reader.read),_key(service.reader.validate)) for kind,service in services.items()}
        self._service_pins=tuple(self._services.items())
        self._paths={path:kind for path,kind in _PATHS.items() if kind in services}
        self._path_pins=tuple(self._paths.items())
        self._base_routes=tuple(base._routes)
        staged=Router(auth)
        if 'config' in services:staged.add('GET','/api/intercept/tool-config',lambda r,p:self._handle(r,'config'))
        if 'rules' in services:staged.add('GET','/api/intercept/rules',lambda r,p:self._handle(r,'rules'))
        if 'judge_usage' in services:staged.add('GET','/api/intercept/judge/usage',lambda r,p:self._handle(r,'judge_usage'))
        if 'notification_history' in services:staged.add('GET','/api/notify/deliveries',lambda r,p:self._handle(r,'notification_history'))
        self._router=staged;self._routes=tuple(staged._routes)
    def _check(self):
        app,base,auth,owner,grant,config,rules,executor,bound=self._pins
        if tuple(self._services.items())!=self._service_pins or tuple(self._paths.items())!=self._path_pins or _judge.parse_request is not _PARSERS[0] or _history.parse_request is not _PARSERS[1]:raise RuntimeError('Metadata bindings changed')
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
    def _data_grants(self,request,kind,selection):
        service=self._services[kind][0]
        self._check()
        if service.current() is not True:raise PermissionError()
        self._check()
        args=(request,) if kind in ('config','rules') else (request,selection)
        if service.authorize(*args) is not True:raise PermissionError()
        self._check()
        if kind=='notification_history':
            if service.raw_authorize(request,selection) is not True:raise PermissionError()
            self._check()
            if service.authorize(request,selection) is not True:raise PermissionError()
            self._check()
        if service.current() is not True:raise PermissionError()
        self._check()
    def _disclose(self,request,kind,selection,identity,request_identity):
        self._check();auth=self._pins[2]
        def selected():
            if _selection_identity(kind,selection)!=identity or (request.method,request.target,request.body)!=request_identity:
                raise RuntimeError('Selected metadata scope changed')
        selected()
        if auth(request) is not True:raise PermissionError()
        self._check();selected()
        if self._pins[4](request,kind) is not True:raise PermissionError()
        self._check();self._data_grants(request,kind,selection);selected()
        if auth(request) is not True:raise PermissionError()
        self._check();selected()
        # Last JWT callback precedes the final exact disclosure grant chain.
        if self._pins[4](request,kind) is not True:raise PermissionError()
        self._check();self._data_grants(request,kind,selection);selected()
        self._check()
    def _handle(self,request,kind):
        try:
            self._check()
            state=_REQUEST_STATE.get()
            if state is None or state.get('request') is not request:raise RuntimeError('Original request context required')
            selection=_PARSERS[0](request) if kind=='judge_usage' else _PARSERS[1](request) if kind=='notification_history' else None
            identity=_selection_identity(kind,selection);request_identity=(request.method,request.target,request.body)
            state.update(selection=selection,identity=identity,request_identity=request_identity)
            self._disclose(request,kind,selection,identity,request_identity)
            service=self._services[kind][0]
            result=service.handle(request,selection=selection) if kind in ('judge_usage','notification_history') else service.handle(request)
            if result.status==200:self._disclose(request,kind,selection,identity,request_identity)
            else:self._check()
            return result
        except (_judge.InvalidRequest,_history.InvalidRequest):return error_response(400,'Invalid metadata request')
        except PermissionError:return error_response(403,'Intercept metadata access denied')
        except Exception:return error_response(503,'Intercept metadata unavailable')
    def __call__(self,environ,start_response):
        path=environ.get('PATH_INFO','')
        if path not in self._paths:return self.original(environ,start_response)
        state={};outer=self
        class Dispatch:
            def dispatch(self,request):
                state['request']=request
                try:outer._check()
                except Exception:return error_response(503,'Intercept metadata unavailable')
                return outer._router.dispatch(request)
        transport=WSGIApplication(Dispatch(),max_body_bytes=self._pins[8])
        def capture(status,headers,exc_info=None):state.update(status=status,headers=headers,exc_info=exc_info)
        token=_REQUEST_STATE.set(state)
        try:body=transport(environ,capture)
        finally:_REQUEST_STATE.reset(token)
        successful=state['status'].startswith('200 ')
        if successful:
            try:self._disclose(state['request'],self._paths[path],state['selection'],state['identity'],state['request_identity'])
            except Exception:
                response=error_response(403,'Intercept metadata access denied')
                body=[response.body];state.update(status='403 Forbidden',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')])
                successful=False
        start_response(state['status'],state['headers'])
        if successful:
            try:self._disclose(state['request'],self._paths[path],state['selection'],state['identity'],state['request_identity'])
            except Exception:raise RuntimeError('Intercept disclosure revoked after response start') from None
        return body

def bind_metadata_reads(application,*,authorize_global,config_service=None,rules_service=None,
                        judge_usage_service=None,notification_history_service=None):
    """Typed optional metadata routes. No defaults, mutation, or generic handlers.

    Each selected service retains its independent read/raw/current grants. The
    global grant names are config, rules, judge_usage, notification_history.
    """
    if type(application) is not WSGIApplication or type(application.router) is not Router:
        raise ValueError('Exact original authenticated application and router required')
    base=application.router;auth=base.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:
        raise ValueError('Original AuthService JWT authorizer required')
    services={kind:service for kind,service in (('config',config_service),('rules',rules_service),
        ('judge_usage',judge_usage_service),('notification_history',notification_history_service)) if service is not None}
    if not services or not callable(authorize_global):raise ValueError('Explicit metadata services and global grant required')
    executor=None
    for kind,service in services.items():
        service_type,reader_type=_TYPES[kind]
        if type(service) is not service_type or type(service.reader) is not reader_type:
            raise ValueError('Exact typed metadata services/readers required')
        if executor is None:executor=service.reader.executor
        elif service.reader.executor is not executor:raise ValueError('Metadata readers must share the original executor')
    paths={path for path,kind in _PATHS.items() if kind in services}
    for method,path,regex,handler,public in base._routes:
        if method in ('GET','HEAD') and any(regex.fullmatch(target) for target in paths):
            raise ValueError('Metadata read route conflicts with original application')
    for service in services.values():
        reader=service.reader
        if not _same(service.handle,(service,type(service).handle)) or not _same(service._check,(service,type(service)._check)) or not _same(reader.read,(reader,type(reader).read)) or not _same(reader.validate,(reader,type(reader).validate)):
            raise ValueError('Original read service methods required')
        service._check()
    view=_InterceptReadApplication(application,services,authorize_global);view._check()
    return MetadataReadBundle(view,config_service,rules_service,judge_usage_service,notification_history_service)

def bind_intercept_reads(application,*,config_service,rules_service,authorize_global):
    """Compatibility entrypoint for the original two explicit intercept reads."""
    if type(config_service) is not InterceptConfigReadService or type(rules_service) is not ToolInterceptReadService:
        raise ValueError('Both original intercept read services required')
    bundle=bind_metadata_reads(application,config_service=config_service,rules_service=rules_service,authorize_global=authorize_global)
    return InterceptReadBundle(bundle.application,config_service,rules_service)
