"""Explicit metadata-only notification PATCH overlay; no default registration.

Original JWT/global/service authority is propagated into every request-authority
checkpoint of the actual store, not just checked around transport. Late failures
never assert rollback or invite a retry. After response headers, revocation aborts
body delivery. These are finite cooperative-policy fences, not atomicity across
arbitrarily mutually mutating callbacks. No sends, credential/config edits, listener
or automatic retries are introduced. This optional regex-dispatched overlay is
not included in the current Router.add-based route inventory tally.
"""
from dataclasses import dataclass,field
import re
from .auth import AuthService
from .core import Router,json_response,error_response
from .wsgi import WSGIApplication
from .notification_channel_metadata import ChannelMetadataService
from ..db.notification_channel_metadata import ChannelMetadataStore,ChannelMetadataPatch,MetadataDenied

_PATH='/api/notify/channels/{id}'
_FAMILY=re.compile(r'/api/notify/channels/[^/]+\Z')

def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)
def _same(method,key):return all(a is b for a,b in zip(_key(method),key))
def _request_key(r):return r.method,r.target,r.body,tuple(r.headers.items())
def _unknown():return json_response(503,{'error':'Channel metadata outcome unknown; reconcile before retry','outcome':'unknown','retry':False})

@dataclass(frozen=True)
class NotificationMetadataBundle:
    application:object=field(repr=False)
    service:ChannelMetadataService=field(repr=False)

class _MetadataApplication:
    def __init__(self,app,service,grant):
        self.original=app;self.service=service;self.authorize_global=grant
        base=app.router;auth=base.authorize;owner=auth.__self__;store=service.store
        self._pins=(app,base,auth,owner,service,store,store.executor,store.scope,grant,app.max_body_bytes)
        self._auth=(owner.store,owner.codec,owner.key,owner.clock)
        self._routes=tuple(base._routes)
        self._methods=tuple((_key(m),obj,name) for obj,name in ((app,'__call__'),(app,'_handle'),(base,'dispatch'),(owner,'authorized'),(service,'handle'),(service,'_check'),(store,'patch'),(store,'validate')) for m in (getattr(obj,name),))
        self._policies=(service.authorize,store.authorize,store.current,store.guard)
    def _check(self):
        app,base,auth,owner,service,store,executor,scope,grant,bound=self._pins
        if self.original is not app or app.router is not base or base.authorize is not auth or self.service is not service or service.store is not store or store.executor is not executor or store.scope is not scope or self.authorize_global is not grant or app.max_body_bytes!=bound or type(app.max_body_bytes) is not int or tuple(base._routes)!=self._routes:raise MetadataDenied()
        if any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),self._auth)):raise MetadataDenied()
        if any(not _same(getattr(obj,name),key) for key,obj,name in self._methods):raise MetadataDenied()
        if any(a is not b for a,b in zip((service.authorize,store.authorize,store.current,store.guard),self._policies)):raise MetadataDenied()
        service._check()
    def _allow(self,state,scope,patch):
        self._check();request=state['request']
        if _request_key(request)!=state['request_key'] or scope is not self._pins[7] or type(patch) is not ChannelMetadataPatch or type(patch.channel_id) is not int or type(patch.changes_json) is not str:raise MetadataDenied()
        identity=(patch.channel_id,patch.changes_json)
        if 'patch' not in state:state.update(patch=patch,patch_key=identity)
        if patch is not state['patch'] or identity!=state['patch_key']:raise MetadataDenied()
        def check():
            self._check()
            if _request_key(request)!=state['request_key'] or (patch.channel_id,patch.changes_json)!=state['patch_key'] or type(patch.channel_id) is not int or type(patch.changes_json) is not str:raise MetadataDenied()
        auth=self._pins[2];grant=self._pins[8];service_grant,store_grant,current,_=self._policies
        check()
        if auth(request) is not True:raise MetadataDenied()
        check()
        if grant(request,scope,patch) is not True:raise MetadataDenied()
        check()
        if service_grant(request,scope,patch) is not True:raise MetadataDenied()
        check()
        if current(scope) is not True:raise MetadataDenied()
        check()
        return True
    def _write_gate(self,state,request,scope,patch):
        # All original source-policy callbacks precede the final outer write grant.
        # There are no callback-bearing current/store gates after this returns.
        self._allow(state,scope,patch)
        if self._policies[1](scope,patch) is not True:raise MetadataDenied()
        self._check()
        if self._policies[2](scope) is not True:raise MetadataDenied()
        self._check()
        if self._policies[0](request,scope,patch) is not True:raise MetadataDenied()
        self._check()
        if self._pins[2](request) is not True:raise MetadataDenied()
        self._check()
        if self._pins[8](request,scope,patch) is not True:raise MetadataDenied()
        self._check()
        if _request_key(request)!=state['request_key'] or patch is not state['patch'] or (patch.channel_id,patch.changes_json)!=state['patch_key'] or type(patch.channel_id) is not int or type(patch.changes_json) is not str:raise MetadataDenied()
        return True
    def _final(self,state):
        return self._write_gate(state,state['request'],self._pins[7],state['patch'])
    def __call__(self,environ,start_response):
        if environ.get('REQUEST_METHOD','').upper()!='PATCH' or not _FAMILY.fullmatch(environ.get('PATH_INFO','')):
            return self.original(environ,start_response)
        state={};outer=self
        class Dispatch:
            def dispatch(self,request):
                state.update(request=request,request_key=_request_key(request))
                try:
                    outer._check()
                    if outer._pins[2](request) is not True:return error_response(401,'Unauthorized')
                    outer._check()
                    # A fresh typed adapter retains the original store's transaction,
                    # normalized proposal and mutation/error semantics. No parser copy.
                    derived=ChannelMetadataService(outer._pins[5],authorize=lambda r,s,p:outer._allow(state,s,p))
                    state['invoked']=True
                    return derived.handle(request,write_authorize=lambda r,s,p:outer._write_gate(state,r,s,p))
                except MetadataDenied:return error_response(403,'Channel metadata mutation denied')
                except Exception:return _unknown() if state.get('invoked') else error_response(503,'Channel metadata unavailable')
        def capture(status,headers,exc_info=None):state.update(status=status,headers=headers)
        body=WSGIApplication(Dispatch(),max_body_bytes=self._pins[9])(environ,capture)
        successful=state['status'].startswith('200 ')
        if successful:
            try:self._final(state)
            except Exception:
                response=_unknown();body=[response.body];state.update(status='503 Service Unavailable',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')]);successful=False
        start_response(state['status'],state['headers'])
        if successful:
            try:self._final(state)
            except Exception:raise RuntimeError('Channel metadata acknowledgement revoked; reconcile before retry') from None
        return body

def bind_notification_metadata(application,*,service,authorize_global):
    """Opt-in one protected PATCH family, preserving the original application."""
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(service) is not ChannelMetadataService or type(service.store) is not ChannelMetadataStore or not callable(authorize_global):raise ValueError('Exact application/service and explicit global write authority required')
    base=application.router;auth=base.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:raise ValueError('Original AuthService JWT required')
    for obj,name,cls in ((service,'handle',ChannelMetadataService),(service,'_check',ChannelMetadataService),(service.store,'patch',ChannelMetadataStore),(service.store,'validate',ChannelMetadataStore)):
        if not _same(getattr(obj,name),(obj,getattr(cls,name))):raise ValueError('Original metadata methods required')
    for method,path,regex,handler,public in base._routes:
        parts=path.strip('/').split('/')
        def matches(part,literal):
            pattern=re.escape(part)
            for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',part):pattern=pattern.replace(re.escape('{'+name+'}'),'[^/]+')
            return re.fullmatch(pattern,literal) is not None
        if method=='PATCH' and len(parts)==4 and all(matches(p,q) for p,q in zip(parts[:3],('api','notify','channels'))):raise ValueError('Channel metadata PATCH conflicts with original route')
    view=_MetadataApplication(application,service,authorize_global);view._check()
    return NotificationMetadataBundle(view,service)
