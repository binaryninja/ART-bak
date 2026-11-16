"""Explicit authenticated skill catalog overlay; no filesystem mutations or default routes.

Finite final order: original JWT, source disclosure/current gates, request and
outer global permission, repeated original JWT, then code-owned snapshot and
pathname/receipt checks, with pure identity pins between steps. No arbitrary
mutually-revoking policy fixed point claim.
"""
from dataclasses import dataclass,field
import re
from .auth import AuthService
from .core import Router,error_response
from .wsgi import WSGIApplication
from .skill_catalog_read import SkillCatalogReadService
from . import skill_catalog_read as _source
from ..skills.catalog_read import SkillCatalogReader,SkillCatalogSnapshot
_PARSER=_source.parse_request
_PATH='/api/skills'
_MATCH=re.compile(r'/api/skills')
_SERVICE_NAMES=('handle','_check','_prepare')
_READER_NAMES=('read','validate','validate_disclosure','_allow','_receipt_check','_snapshot_check','_scan','_content_authority_check')
_SERVICE_METHODS=tuple(getattr(SkillCatalogReadService,n) for n in _SERVICE_NAMES)
_READER_METHODS=tuple(getattr(SkillCatalogReader,n) for n in _READER_NAMES)
def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)
def _same(method,key):return all(a is b for a,b in zip(_key(method),key))
def _request(r):return r.method,r.target,r.body,tuple(r.headers.items())

@dataclass(frozen=True)
class SkillCatalogReadBundle:
    application:object=field(repr=False)
    catalog:SkillCatalogReadService=field(repr=False)

class _SkillCatalogApplication:
    def __init__(self,app,service,grant):
        self.original=app;self.catalog=service;self.authorize_global=grant
        base=app.router;auth=base.authorize;owner=auth.__self__;reader=service.reader
        self._pins=(app,base,auth,owner,service,reader,reader.scope.root,reader.scope,grant,app.max_body_bytes)
        self._auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
        self._policies=(service.authorize,reader.authorize_global,reader.current,reader.guard)
        self._routes=tuple(base._routes)
        pairs=((app,'__call__'),(app,'_handle'),(base,'dispatch'),(owner,'authorized'),(service,'handle'),(service,'_check'),(service,'_prepare'),(reader,'read'),(reader,'validate'),(reader,'validate_disclosure'),(reader,'_allow'),(reader,'_receipt_check'),(reader,'_snapshot_check'),(reader,'_scan'),(reader,'_content_authority_check'))
        self._methods=tuple((_key(getattr(o,n)),o,n) for o,n in pairs)
    def _check(self):
        app,base,auth,owner,svc,reader,executor,scope,grant,bound=self._pins
        if _source.parse_request is not _PARSER or self.original is not app or app.router is not base or base.authorize is not auth or self.catalog is not svc or svc.reader is not reader or reader.scope.root is not executor or reader.scope is not scope or self.authorize_global is not grant or type(app.max_body_bytes) is not int or app.max_body_bytes!=bound or tuple(base._routes)!=self._routes:raise PermissionError()
        if any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),self._auth_pins)) or any(a is not b for a,b in zip((svc.authorize,reader.authorize_global,reader.current,reader.guard),self._policies)):raise PermissionError()
        if any(not _same(getattr(o,n),k) for k,o,n in self._methods):raise PermissionError()
        svc._check()
    def __call__(self,environ,start_response):
        if not _MATCH.fullmatch(environ.get('PATH_INFO','')):return self.original(environ,start_response)
        outer=self;state={};result={}
        def pure():
            outer._check()
            req=state['request']
            if req.headers is not state['headers'] or _request(req)!=state['request_key']:raise PermissionError()
        def grant(req,scope):
            outer._check()
            if req is not state['request'] or scope is not outer._pins[7]:raise PermissionError()
            pure()
            if outer._policies[0](req,scope) is not True:raise PermissionError()
            pure()
            if outer._pins[2](req) is not True:raise PermissionError()
            pure()
            if outer._pins[8](req,scope) is not True:raise PermissionError()
            pure();return True
        def handle(req):
            try:
                outer._check();state.update(request=req,request_key=_request(req),headers=req.headers)
                # Exact service, original reader: composed authority runs at the
                # original source read boundaries, not merely around dispatch.
                derived=SkillCatalogReadService(outer._pins[5],authorize=grant)
                response,snapshot=derived._prepare(req)
                if response.status==200:
                    if type(snapshot) is not SkillCatalogSnapshot:raise PermissionError()
                    state['snapshot']=snapshot
                return response
            except Exception:return error_response(403,'Skill catalog denied')
        class Dispatch:
            def dispatch(self,req):
                try:
                    outer._check()
                    if outer._pins[2](req) is not True:return error_response(401,'Unauthorized')
                    outer._check()
                    if req.method not in ('GET','HEAD'):
                        response=error_response(405,'Method not allowed')
                        return type(response)(response.status,response.body,{**response.headers,'Allow':'GET, HEAD'})
                    return handle(req)
                except Exception:return error_response(503,'Skill catalog unavailable')
        def capture(status,headers,exc_info=None):result.update(status=status,headers=headers)
        body=WSGIApplication(Dispatch(),max_body_bytes=self._pins[9])(environ,capture)
        successful=result['status'].startswith('200 ')
        def disclose():
            pure();req=state['request']
            if outer._pins[2](req) is not True:raise PermissionError()
            pure()
            outer._pins[5].validate_disclosure(state['snapshot'],disclosure=lambda:grant(req,outer._pins[7]))
            pure()
            # Last source current callback can revoke outer permissions.
            if outer._policies[0](req,outer._pins[7]) is not True:raise PermissionError()
            pure()
            if outer._pins[8](req,outer._pins[7]) is not True:raise PermissionError()
            pure()
            # Outer callbacks can revoke metadata-content authority too.
            outer._pins[5]._content_authority_check(state['snapshot'])
            pure()
            # Time may advance during final authority callbacks; do not rely
            # on the earlier JWT observation when releasing the buffered body.
            if outer._pins[2](req) is not True:raise PermissionError()
            pure()
            outer._pins[5]._snapshot_check(state['snapshot'])
            outer._pins[5]._receipt_check(state['snapshot'])
            pure()
        if successful:
            try:disclose()
            except Exception:
                response=error_response(403,'Skill catalog denied');body=[response.body]
                result.update(status='403 Forbidden',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')]);successful=False
        start_response(result['status'],result['headers'])
        if successful:
            try:disclose()
            except Exception:raise RuntimeError('Skill catalog disclosure revoked after response start') from None
        return body

_VIEW_METHODS=(_SkillCatalogApplication.__init__,_SkillCatalogApplication.__call__,_SkillCatalogApplication._check)

def bind_skill_catalog_reads(application,*,catalog_service,authorize_global):
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(catalog_service) is not SkillCatalogReadService or type(catalog_service.reader) is not SkillCatalogReader or not callable(authorize_global):raise ValueError('Exact application and explicit global skill catalog authority required')
    auth=application.router.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:raise ValueError('Original JWT authority required')
    for method,path,regex,handler,public in application.router._routes:
        if regex.fullmatch(_PATH):raise ValueError('Skill catalog route conflict')
    if any(actual is not expected for actual,expected in zip((_SkillCatalogApplication.__init__,_SkillCatalogApplication.__call__,_SkillCatalogApplication._check),_VIEW_METHODS)):raise ValueError('Original skill catalog implementation required')
    for obj,names,methods in ((catalog_service,_SERVICE_NAMES,_SERVICE_METHODS),(catalog_service.reader,_READER_NAMES,_READER_METHODS)):
        if any(not _same(getattr(obj,n),(obj,method)) for n,method in zip(names,methods)):raise ValueError('Original skill catalog methods required')
    view=_SkillCatalogApplication(application,catalog_service,authorize_global);view._check()
    return SkillCatalogReadBundle(view,catalog_service)
