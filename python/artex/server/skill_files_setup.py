"""Explicit authenticated skill file listing overlay; no filesystem or default routes.

Finite final order: JWT, original request/source/current gates, outer global
permission, pure pins. No arbitrary mutually-revoking policy fixed point claim.
"""
from dataclasses import dataclass,field
import re
from .auth import AuthService
from .core import Router,error_response
from .wsgi import WSGIApplication
from . import skill_read_transport as _transport
_DECODE=_transport.decode_skill_request
from .skill_files_read import SkillFilesReadService
from . import skill_files_read as _source
from ..skills.file_listing import SkillFileListingReader
_PARSER=_source.parse_request
_PATH='/api/skills/{name}/files'
_MATCH=re.compile(r'/api/skills/[^/]+/files')
def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)
def _same(method,key):return all(a is b for a,b in zip(_key(method),key))
def _request(r):return r.method,r.target,r.body,tuple(r.headers.items())
def _overlap(path):
    parts=path.split('/')
    if len(parts)!=5:return False
    for actual,expected in zip(parts,('', 'api','skills',None,'files')):
        if expected is None:continue
        pattern=re.escape(actual)
        for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',actual):pattern=pattern.replace(re.escape('{'+name+'}'),r'[^/]+')
        if re.fullmatch(pattern,expected) is None:return False
    return True

@dataclass(frozen=True)
class SkillFilesReadBundle:
    application:object=field(repr=False)
    files:SkillFilesReadService=field(repr=False)

class _SkillFilesApplication:
    def __init__(self,app,service,grant):
        self.original=app;self.files=service;self.authorize_global=grant
        base=app.router;auth=base.authorize;owner=auth.__self__;reader=service.reader
        self._pins=(app,base,auth,owner,service,reader,reader.scope.root,reader.scope,grant,app.max_body_bytes)
        self._auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
        self._policies=(service.authorize,reader.authorize,reader.current,reader.guard)
        self._routes=tuple(base._routes)
        pairs=((app,'__call__'),(app,'_handle'),(base,'dispatch'),(owner,'authorized'),(service,'handle'),(service,'_check'),(reader,'read'),(reader,'validate'),(reader,'validate_disclosure'),(reader,'_allow'),(reader,'_root_current'))
        self._methods=tuple((_key(getattr(o,n)),o,n) for o,n in pairs)
    def _check(self):
        app,base,auth,owner,svc,reader,executor,scope,grant,bound=self._pins
        if _transport.decode_skill_request is not _DECODE or _source.parse_request is not _PARSER or self.original is not app or app.router is not base or base.authorize is not auth or self.files is not svc or svc.reader is not reader or reader.scope.root is not executor or reader.scope is not scope or self.authorize_global is not grant or type(app.max_body_bytes) is not int or app.max_body_bytes!=bound or tuple(base._routes)!=self._routes:raise PermissionError()
        if any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),self._auth_pins)) or any(a is not b for a,b in zip((svc.authorize,reader.authorize,reader.current,reader.guard),self._policies)):raise PermissionError()
        if any(not _same(getattr(o,n),k) for k,o,n in self._methods):raise PermissionError()
        svc._check()
    def __call__(self,environ,start_response):
        if not _MATCH.fullmatch(environ.get('PATH_INFO','')):return self.original(environ,start_response)
        outer=self;state={};result={}
        head=str(environ.get('REQUEST_METHOD','')).upper()=='HEAD'
        def pure():
            outer._check()
            req=state['request'];sel=state['selection']
            if req.headers is not state['headers'] or _request(req)!=state['request_key'] or type(sel) is not str or sel!=state['identity']:raise PermissionError()
        def grant(req,scope,selection):
            outer._check()
            if req is not state['request'] or scope is not outer._pins[7] or type(selection) is not str:raise PermissionError()
            if 'selection' not in state:state.update(selection=selection,identity=selection)
            if selection is not state['selection']:raise PermissionError()
            pure()
            if outer._policies[0](req,scope,selection) is not True:raise PermissionError()
            pure()
            if outer._pins[2](req) is not True:raise PermissionError()
            pure()
            if outer._pins[8](req,scope,selection) is not True:raise PermissionError()
            pure();return True
        def handle(req):
            try:
                outer._check();state.update(request=req,request_key=_request(req),headers=req.headers)
                # Exact service, original reader: composed authority runs at the
                # original source read boundaries, not merely around dispatch.
                derived=SkillFilesReadService(outer._pins[5],authorize=grant)
                return derived.handle(req)
            except Exception:return error_response(403,'Skill file listing denied')
        staged=Router(self._pins[2])
        staged.add('GET','/api/skills/{name}/files',lambda r,p:handle(r))
        class Dispatch:
            def dispatch(self,req):
                try:outer._check()
                except Exception:return error_response(503,'Skill file listing unavailable')
                if outer._pins[2](req) is not True:return error_response(401,'Unauthorized')
                try:decoded=_DECODE(req)
                except (ValueError,UnicodeError):return error_response(400,'Invalid skill file listing request')
                return staged.dispatch(decoded)
        def capture(status,headers,exc_info=None):result.update(status=status,headers=headers)
        body=WSGIApplication(Dispatch(),max_body_bytes=self._pins[9])(environ,capture)
        successful=result['status'].startswith('200 ')
        def disclose():
            pure();req=state['request'];sel=state['selection']
            if outer._pins[2](req) is not True:raise PermissionError()
            pure()
            outer._pins[5].validate_disclosure(sel,disclosure=lambda:grant(req,outer._pins[7],sel))
            pure()
            # Last source current callback can revoke outer permissions.
            if outer._policies[0](req,outer._pins[7],sel) is not True:raise PermissionError()
            pure()
            if outer._pins[8](req,outer._pins[7],sel) is not True:raise PermissionError()
            pure()
            # Time may advance during final authority callbacks; do not rely
            # on the earlier JWT observation when releasing the buffered body.
            if outer._pins[2](req) is not True:raise PermissionError()
            pure()
            outer._pins[5]._root_current()
            pure()
        if successful:
            try:disclose()
            except Exception:
                response=error_response(403,'Skill file listing denied');body=[] if head else [response.body]
                result.update(status='403 Forbidden',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')]);successful=False
        start_response(result['status'],result['headers'])
        if successful:
            try:disclose()
            except Exception:raise RuntimeError('Skill file listing disclosure revoked after response start') from None
        return body

def bind_skill_files_reads(application,*,files_service,authorize_global):
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(files_service) is not SkillFilesReadService or type(files_service.reader) is not SkillFileListingReader or not callable(authorize_global):raise ValueError('Exact application and explicit global skill file listing authority required')
    auth=application.router.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:raise ValueError('Original JWT authority required')
    for method,path,regex,handler,public in application.router._routes:
        if method in ('GET','HEAD') and _overlap(path):raise ValueError('Skill file listing route conflict')
    for obj,names,cls in ((files_service,('handle','_check'),SkillFilesReadService),(files_service.reader,('read','validate','validate_disclosure','_allow','_root_current'),SkillFileListingReader)):
        if any(not _same(getattr(obj,n),(obj,getattr(cls,n))) for n in names):raise ValueError('Original skill file listing methods required')
    view=_SkillFilesApplication(application,files_service,authorize_global);view._check()
    return SkillFilesReadBundle(view,files_service)
