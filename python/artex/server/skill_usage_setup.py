"""Explicit authenticated skill usage overlay; no filesystem or default routes.

Finite final order: JWT, original request/source/current gates, outer global
permission, pure pins. No arbitrary mutually-revoking policy fixed point claim.
"""
from dataclasses import dataclass,field
import re
from .auth import AuthService
from .core import Router,error_response
from .wsgi import WSGIApplication
from .skill_usage_read import SkillUsageReadService
from . import skill_usage_read as _source
from ..db.skill_usage_read import SkillUsageReader,UsageSelection
_PARSER=_source.parse_request
_PATH='/api/skills/{name}/usage'
_MATCH=re.compile(r'/api/skills/[^/]+/usage')
def _key(method):return getattr(method,'__self__',None),getattr(method,'__func__',method)
def _same(method,key):return all(a is b for a,b in zip(_key(method),key))
def _request(r):return r.method,r.target,r.body,tuple(r.headers.items())
def _overlap(path):
    parts=path.split('/')
    if len(parts)!=5:return False
    for actual,expected in zip(parts,('', 'api','skills',None,'usage')):
        if expected is None:continue
        pattern=re.escape(actual)
        for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',actual):pattern=pattern.replace(re.escape('{'+name+'}'),r'[^/]+')
        if re.fullmatch(pattern,expected) is None:return False
    return True

@dataclass(frozen=True)
class SkillUsageReadBundle:
    application:object=field(repr=False)
    usage:SkillUsageReadService=field(repr=False)

class _SkillUsageApplication:
    def __init__(self,app,service,grant):
        self.original=app;self.usage=service;self.authorize_global=grant
        base=app.router;auth=base.authorize;owner=auth.__self__;reader=service.reader
        self._pins=(app,base,auth,owner,service,reader,reader.executor,reader.scope,grant,app.max_body_bytes)
        self._auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
        self._policies=(service.authorize,reader.authorize_global,reader.current,reader.guard)
        self._routes=tuple(base._routes)
        pairs=((app,'__call__'),(app,'_handle'),(base,'dispatch'),(owner,'authorized'),(service,'handle'),(service,'_check'),(reader,'read'),(reader,'validate'),(reader,'validate_disclosure'),(reader,'_allow'))
        self._methods=tuple((_key(getattr(o,n)),o,n) for o,n in pairs)
    def _check(self):
        app,base,auth,owner,svc,reader,executor,scope,grant,bound=self._pins
        if _source.parse_request is not _PARSER or self.original is not app or app.router is not base or base.authorize is not auth or self.usage is not svc or svc.reader is not reader or reader.executor is not executor or reader.scope is not scope or self.authorize_global is not grant or type(app.max_body_bytes) is not int or app.max_body_bytes!=bound or tuple(base._routes)!=self._routes:raise PermissionError()
        if any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),self._auth_pins)) or any(a is not b for a,b in zip((svc.authorize,reader.authorize_global,reader.current,reader.guard),self._policies)):raise PermissionError()
        if any(not _same(getattr(o,n),k) for k,o,n in self._methods):raise PermissionError()
        svc._check()
    def __call__(self,environ,start_response):
        if not _MATCH.fullmatch(environ.get('PATH_INFO','')):return self.original(environ,start_response)
        outer=self;state={};result={}
        def pure():
            outer._check()
            req=state['request'];sel=state['selection']
            if req.headers is not state['headers'] or _request(req)!=state['request_key'] or type(sel) is not UsageSelection or (sel.name,sel.limit)!=state['identity']:raise PermissionError()
            sel.validate()
        def grant(req,scope,selection):
            outer._check()
            if req is not state['request'] or scope is not outer._pins[7] or type(selection) is not UsageSelection:raise PermissionError()
            if 'selection' not in state:state.update(selection=selection,identity=(selection.name,selection.limit))
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
                derived=SkillUsageReadService(outer._pins[5],authorize=grant)
                return derived.handle(req)
            except Exception:return error_response(403,'Skill usage denied')
        staged=Router(self._pins[2])
        staged.add('GET','/api/skills/{name}/usage',lambda r,p:handle(r))
        class Dispatch:
            def dispatch(self,req):
                try:outer._check()
                except Exception:return error_response(503,'Skill usage unavailable')
                return staged.dispatch(req)
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
        if successful:
            try:disclose()
            except Exception:
                response=error_response(403,'Skill usage denied');body=[response.body]
                result.update(status='403 Forbidden',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')]);successful=False
        start_response(result['status'],result['headers'])
        if successful:
            try:disclose()
            except Exception:raise RuntimeError('Skill usage disclosure revoked after response start') from None
        return body

def bind_skill_usage_reads(application,*,usage_service,authorize_global):
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(usage_service) is not SkillUsageReadService or type(usage_service.reader) is not SkillUsageReader or not callable(authorize_global):raise ValueError('Exact application and explicit global skill usage authority required')
    auth=application.router.authorize;owner=getattr(auth,'__self__',None)
    if type(owner) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:raise ValueError('Original JWT authority required')
    for method,path,regex,handler,public in application.router._routes:
        if method in ('GET','HEAD') and _overlap(path):raise ValueError('Skill usage route conflict')
    for obj,names,cls in ((usage_service,('handle','_check'),SkillUsageReadService),(usage_service.reader,('read','validate','validate_disclosure','_allow'),SkillUsageReader)):
        if any(not _same(getattr(obj,n),(obj,getattr(cls,n))) for n in names):raise ValueError('Original skill usage methods required')
    view=_SkillUsageApplication(application,usage_service,authorize_global);view._check()
    return SkillUsageReadBundle(view,usage_service)
