"""Opt-in literal tool-policy writes; no guard installation or default routes.

One exclusively owned PostgresExecutor, one declared policy owner. Unknown
postattempt outcomes are reconciliation-only, never an automatic write retry.
Finite authorization fences assume cooperative callbacks; they are not a fixed
point over mutually revoking grants. A returned body is not network delivery.
"""
from dataclasses import dataclass,field
import re
from .auth import AuthService
from .core import Router,error_response,json_response
from .wsgi import WSGIApplication
from .tool_intercept_mutation import ToolInterceptMutationService
from ..db.tool_intercept_config import ToolInterceptConfigStore,PolicyMutation,PolicyDenied,mutation_identity
from ..db.postgres import PostgresExecutor
from ..intercept.policy_owner import InterceptPolicyOwner
from ..intercept import Interceptor
_ROUTES=(('POST','/api/intercept/rules'),('PUT','/api/intercept/rules/{id}'),('DELETE','/api/intercept/rules/{id}'),('POST','/api/intercept/rules/{id}/toggle'),('PUT','/api/intercept/tool-config'))
def _key(m):return getattr(m,'__self__',None),getattr(m,'__func__',m)
def _same(m,k):return all(a is b for a,b in zip(_key(m),k))
def _request(r):return r.method,r.target,r.body,tuple(r.headers.items())
def _segment(segment):
    value=re.escape(segment)
    for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',segment):value=value.replace(re.escape('{'+name+'}'),r'[^/]+')
    return value
def _overlap(a,b):
    aa=a.split('/');bb=b.split('/')
    if len(aa)!=len(bb):return False
    for x,y in zip(aa,bb):
        if '{' in x and '{' in y:continue
        if '{' in x:
            if re.fullmatch(_segment(x),y) is None:return False
        elif '{' in y:
            if re.fullmatch(_segment(y),x) is None:return False
        elif x!=y:return False
    return True
@dataclass(frozen=True)
class ToolInterceptMutationBundle:
    application:object=field(repr=False)
    mutation:ToolInterceptMutationService=field(repr=False)

class _MutationApplication:
    def __init__(self,app,service,grant):
        self.original=app;self.mutation=service;self.authorize_global=grant;self._unresolved=False
        base=app.router;auth=base.authorize;authowner=auth.__self__;owner=service.owner;store=owner.store;db=store.executor
        self._pins=(app,base,auth,authowner,service,owner,store,db,grant,app.max_body_bytes)
        self._owner_lock=owner._lock
        self._auth=(authowner.store,authowner.codec,authowner.key,authowner.clock)
        self._db=(db._connection,db._cursor_factory,db._lock)
        self._policies=(service.authorize,owner.authorize,owner.current,owner.idle,owner.guard)
        self._routes=tuple(base._routes)
        pairs=((app,'__call__'),(app,'_handle'),(base,'dispatch'),(authowner,'authorized'),(service,'handle'),(service,'_check'),(owner,'mutate'),(owner,'_allow'),(owner,'validate'),(store,'_apply'),(store,'validate'),(db,'transaction'),(db,'execute'),(db,'fetch_all'),(db,'lock_intercept_settings'))
        self._methods=tuple((_key(getattr(o,n)),o,n) for o,n in pairs)
    def _quarantine(self):
        # Retain the original shared owner, not only this HTTP view: rebinding a
        # second view must not turn an unacknowledged write into a safe retry.
        with self._owner_lock:
            self._unresolved=True
            self._pins[5].unresolved=True
            self._pins[6]._unresolved=True
    def _check(self):
        app,base,auth,ao,svc,owner,store,db,grant,bound=self._pins
        if self._unresolved:raise PolicyDenied()
        if owner._lock is not self._owner_lock:raise PolicyDenied()
        if self.original is not app or app.router is not base or base.authorize is not auth or self.mutation is not svc or svc.owner is not owner or owner.store is not store or store.executor is not db or self.authorize_global is not grant or type(app.max_body_bytes) is not int or app.max_body_bytes!=bound or tuple(base._routes)!=self._routes:raise PolicyDenied()
        if db._owns_connection is not True or db._closed or db._connection.closed:raise PolicyDenied()
        if any(a is not b for a,b in zip((db._connection,db._cursor_factory,db._lock),self._db)) or any(a is not b for a,b in zip((ao.store,ao.codec,ao.key,ao.clock),self._auth)) or any(a is not b for a,b in zip((svc.authorize,owner.authorize,owner.current,owner.idle,owner.guard),self._policies)):raise PolicyDenied()
        if any(not _same(getattr(o,n),k) for k,o,n in self._methods):raise PolicyDenied()
        svc._check()
    def __call__(self,environ,start_response):
        path=environ.get('PATH_INFO','')
        if not any(environ.get('REQUEST_METHOD','GET')==m and re.fullmatch('/'.join(_segment(s) for s in p.split('/')),path) for m,p in _ROUTES):return self.original(environ,start_response)
        outer=self;state={};result={}
        def pure():
            outer._check()
            req=state['request']
            if req.headers is not state['headers'] or _request(req)!=state['request_key']:raise PolicyDenied()
            if 'selection' in state and mutation_identity(state['selection'])!=state['identity']:raise PolicyDenied()
        def grant(req,selection):
            outer._check()
            if req is not state['request'] or type(selection) is not PolicyMutation:raise PolicyDenied()
            if 'selection' not in state:state.update(selection=selection,identity=mutation_identity(selection))
            if selection is not state['selection']:raise PolicyDenied()
            pure()
            if outer._policies[0](req,selection) is not True:raise PolicyDenied()
            pure()
            if outer._pins[2](req) is not True:raise PolicyDenied()
            pure()
            if outer._pins[8](req,selection) is not True:raise PolicyDenied()
            pure();return True
        def handle(operation,req,params):
            try:
                outer._check();state.update(request=req,request_key=_request(req),headers=req.headers)
                raw=params.get('id');rule_id=None
                if raw is not None:
                    if not re.fullmatch(r'[1-9][0-9]{0,18}',raw) or int(raw)>=2**63:return error_response(400,'Invalid rule id')
                    rule_id=int(raw)
                derived=ToolInterceptMutationService(outer._pins[5],authorize=grant)
                response=derived.handle(operation,req,rule_id)
                # The standalone service deliberately reports an unknown
                # outcome as 503/retry:false, including acknowledgement loss
                # after owner.mutate has already returned and committed.
                if response.status==503:outer._quarantine()
                return response
            except Exception:return error_response(403,'Policy mutation denied')
        staged=Router(self._pins[2])
        staged.add('POST','/api/intercept/rules',lambda r,p:handle('create',r,p))
        staged.add('PUT','/api/intercept/rules/{id}',lambda r,p:handle('update',r,p))
        staged.add('DELETE','/api/intercept/rules/{id}',lambda r,p:handle('delete',r,p))
        staged.add('POST','/api/intercept/rules/{id}/toggle',lambda r,p:handle('toggle',r,p))
        staged.add('PUT','/api/intercept/tool-config',lambda r,p:handle('tools',r,p))
        class Dispatch:
            def dispatch(self,req):
                try:outer._check()
                except Exception:return error_response(503,'Policy mutation unavailable')
                return staged.dispatch(req)
        def capture(status,headers,exc_info=None):result.update(status=status,headers=headers)
        body=WSGIApplication(Dispatch(),max_body_bytes=self._pins[9])(environ,capture)
        success=result['status'].startswith('200 ')
        def final():
            pure();selection=state['selection'];req=state['request']
            outer._pins[5]._allow(selection,lambda m:grant(req,m),state['identity'])
            pure()
        if success:
            try:final()
            except BaseException:
                self._quarantine()
                response=json_response(503,{'error':'Policy outcome requires reconciliation','retry':False});body=[response.body]
                result.update(status='503 Service Unavailable',headers=[('Content-Type','application/json'),('Content-Length',str(len(response.body))),('X-Content-Type-Options','nosniff')]);success=False
        try:start_response(result['status'],result['headers'])
        except BaseException:
            if success:self._quarantine()
            raise
        if success:
            try:final()
            except BaseException:
                self._quarantine()
                raise RuntimeError('Policy acknowledgement revoked after response start; reconcile without retry') from None
        return body

def bind_tool_intercept_mutations(application,*,mutation_service,authorize_global):
    if type(application) is not WSGIApplication or type(application.router) is not Router or type(mutation_service) is not ToolInterceptMutationService or type(mutation_service.owner) is not InterceptPolicyOwner or type(mutation_service.owner.store) is not ToolInterceptConfigStore or type(mutation_service.owner.store.executor) is not PostgresExecutor or not callable(authorize_global):raise ValueError('Exact application/policy owner/owned PostgresExecutor required')
    db=mutation_service.owner.store.executor
    if db._owns_connection is not True:raise ValueError('Exclusively owned session required')
    auth=application.router.authorize;ao=getattr(auth,'__self__',None)
    if type(ao) is not AuthService or getattr(auth,'__func__',None) is not AuthService.authorized:raise ValueError('Original JWT required')
    for method,path,*_ in application.router._routes:
        if any(method==m and _overlap(path,p) for m,p in _ROUTES):raise ValueError('Policy route conflict')
    for obj,names,cls in ((mutation_service,('handle','_check'),ToolInterceptMutationService),(mutation_service.owner,('mutate','_allow','validate'),InterceptPolicyOwner),(mutation_service.owner.store,('_apply','validate'),ToolInterceptConfigStore),(db,('transaction','execute','fetch_all','lock_intercept_settings'),PostgresExecutor)):
        if any(not _same(getattr(obj,n),(obj,getattr(cls,n))) for n in names):raise ValueError('Original policy methods required')
    view=_MutationApplication(application,mutation_service,authorize_global);view._check()
    return ToolInterceptMutationBundle(view,mutation_service)
