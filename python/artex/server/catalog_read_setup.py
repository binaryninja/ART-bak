"""Optional typed catalog GET overlay, no expansion/send or default activation.

A real protected Router declaration preserves JWT routing. The service issues the
prepared receipt; the wrapper never constructs a receipt or reconstructs its asset
scope from the twenty visible items. Finite policy ordering is JWT, source/asset/
current checks, then final outer grant and pure pins; it is not an atomic
fixed point across arbitrarily mutually mutating policy callbacks. All twenty-one candidate IDs remain subject
to the original grants after headers. No task-report or generic-service fallback.
"""
from dataclasses import dataclass,field
from .auth import AuthService
from .core import Router,error_response
from .wsgi import WSGIApplication
from .chat_mentions import ChatMentionService,PreparedMentionRead
from . import chat_mentions as _mentions
_PARSER=_mentions.parse_request
from ..db.chat_mentions import ChatMentionReader,MentionSelection

_PATH='/api/chat/mentions'
def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)
def _same(method,key):return all(a is b for a,b in zip(_key(method),key))
def _request_key(r):return r.method,r.target,r.body,tuple(r.headers.items())
@dataclass(frozen=True)
class CatalogReadBundle:
    application:object=field(repr=False)
    mentions:ChatMentionService=field(repr=False)

class _CatalogApplication:
    def __init__(self,app,service,grant):
        self.original=app;self.mentions=service;self.authorize_global=grant
        base=app.router;auth=base.authorize;owner=auth.__self__;reader=service.reader
        self._pins=(app,base,auth,owner,service,reader,reader.executor,reader.scope,grant,app.max_body_bytes)
        self._auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
        self._policies=(service.authorize,reader.authorize,reader.asset_authorize,reader.current,reader.guard)
        self._routes=tuple(base._routes)
        pairs=((app,'__call__'),(app,'_handle'),(base,'dispatch'),(owner,'authorized'),(service,'handle'),(service,'prepare'),(service,'validate_prepared'),(service,'check_prepared'),(service,'_issue'),(service,'_check'),(reader,'read'),(reader,'validate'),(reader,'validate_disclosure'))
        self._methods=tuple((_key(getattr(o,n)),o,n) for o,n in pairs)
    def _check(self):
        app,base,auth,owner,service,reader,executor,scope,grant,bound=self._pins
        if _mentions.parse_request is not _PARSER:raise PermissionError()
        if self.original is not app or app.router is not base or base.authorize is not auth or self.mentions is not service or service.reader is not reader or reader.executor is not executor or reader.scope is not scope or self.authorize_global is not grant or type(app.max_body_bytes) is not int or app.max_body_bytes!=bound or tuple(base._routes)!=self._routes:raise PermissionError()
        if any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),self._auth_pins)) or any(a is not b for a,b in zip((service.authorize,reader.authorize,reader.asset_authorize,reader.current,reader.guard),self._policies)):raise PermissionError()
        if any(not _same(getattr(o,n),k) for k,o,n in self._methods):raise PermissionError()
        service._check()
    def _grant(self,state,request,scope,selection):
        self._check()
        if request is not state['request'] or _request_key(request)!=state['request_key'] or scope is not self._pins[7] or type(selection) is not MentionSelection:raise PermissionError()
        identity=(selection.kind,selection.query,selection.cursor)
        if 'selection' not in state:state.update(selection=selection,identity=identity)
        def check():
            self._check()
            if selection is not state['selection'] or (selection.kind,selection.query,selection.cursor)!=state['identity'] or _request_key(request)!=state['request_key']:raise PermissionError()
            selection.validate()
        check()
        if self._pins[2](request) is not True:raise PermissionError()
        check()
        if self._pins[8](request,scope,selection) is not True:raise PermissionError()
        check();return True
    def __call__(self,environ,start_response):
        if environ.get('PATH_INFO','')!=_PATH:return self.original(environ,start_response)
        state={};result={};outer=self
        def handle(request):
            state.update(request=request,request_key=_request_key(request))
            try:
                outer._check()
                receipt=outer._pins[4].prepare(request,disclosure=lambda r,s,q:outer._grant(state,r,s,q))
                if type(receipt) is not PreparedMentionRead:raise PermissionError()
                outer._pins[4].validate_prepared(receipt);outer._check()
                result['receipt']=receipt
                return receipt.response
            except PermissionError:return error_response(403,'Catalog read denied')
        staged=Router(self._pins[2])
        staged.add('GET','/api/chat/mentions',lambda r,p:handle(r))
        class Dispatch:
            def dispatch(self,request):
                try:outer._check()
                except Exception:return error_response(503,'Catalog read unavailable')
                return staged.dispatch(request)
        def capture(status,headers,exc_info=None):result.update(status=status,headers=headers)
        body=WSGIApplication(Dispatch(),max_body_bytes=self._pins[9])(environ,capture)
        successful=result['status'].startswith('200 ')
        def disclose():
            outer._check();outer._pins[4].validate_prepared(result['receipt']);outer._check()
            # The source's final current/guard callbacks may revoke outer access.
            # Refresh only that original grant here, with no later callback-bearing
            # JWT/source gate. Those were checked above with all lookahead IDs.
            if outer._pins[8](state['request'],outer._pins[7],state['selection']) is not True:raise PermissionError()
            outer._check();outer._pins[4].check_prepared(result['receipt'])
            if _request_key(state['request'])!=state['request_key'] or (state['selection'].kind,state['selection'].query,state['selection'].cursor)!=state['identity']:raise PermissionError()
        if successful:
            try:disclose()
            except Exception:
                response=error_response(403,'Catalog read denied');body=[response.body]
                result.update(status='403 Forbidden',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')]);successful=False
        start_response(result['status'],result['headers'])
        if successful:
            try:disclose()
            except Exception:raise RuntimeError('Catalog disclosure revoked after response start') from None
        return body

def bind_catalog_reads(application,*,mentions_service,authorize_global):
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(mentions_service) is not ChatMentionService or type(mentions_service.reader) is not ChatMentionReader or not callable(authorize_global):raise ValueError('Exact catalog service/application and explicit global authority required')
    auth=application.router.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:raise ValueError('Original AuthService JWT required')
    for method,path,regex,handler,public in application.router._routes:
        if method in ('GET','HEAD') and regex.fullmatch(_PATH):raise ValueError('Catalog route conflicts with original application')
    for obj,names,cls in ((mentions_service,('handle','prepare','validate_prepared','check_prepared','_issue','_check'),ChatMentionService),(mentions_service.reader,('read','validate','validate_disclosure'),ChatMentionReader)):
        if any(not _same(getattr(obj,n),(obj,getattr(cls,n))) for n in names):raise ValueError('Original catalog methods required')
    view=_CatalogApplication(application,mentions_service,authorize_global);view._check()
    return CatalogReadBundle(view,mentions_service)
