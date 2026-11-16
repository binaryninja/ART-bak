"""Opt-in archive overview metadata only, no raw diagnostics or archive actions."""
import re
from urllib.parse import parse_qsl,urlsplit
from .core import Router,json_response,error_response
from .auth import AuthService
_AUTHORIZED=AuthService.authorized
from .wsgi import WSGIApplication
from .disclosure import DisclosureResponse
from ..db.archive_catalog import ArchiveCatalogReader
from ..db.archives import STATES

class ArchiveCatalogService:
    def __init__(self,executor,*,workspace,authorize,request_authorize,lifecycle_guard):
        if type(workspace)is not str or not 1<=len(workspace.encode())<=256 or any(not callable(f) for f in (authorize,request_authorize,lifecycle_guard)):
            raise ValueError('Explicit global archive metadata authority required')
        self.executor=executor;self._pins=(executor,workspace,authorize,request_authorize,lifecycle_guard);self._original=self._pins
    def handle(self,request,transport,transport_check):
        e,w,source,http,guard=self._original
        headers=request.headers;identity=(request.method,request.target,request.body,tuple(headers.items()))
        def pure():
            transport_check()
            if self._pins is not self._original or self.executor is not e or request.headers is not headers or (request.method,request.target,request.body,tuple(request.headers.items()))!=identity:raise PermissionError()
        def grants():
            pure()
            if source(w) is not True or http(request,w) is not True:raise PermissionError()
            pure()
        def receipt():
            grants()
            if transport(request)is not True:raise PermissionError()
            grants();pure()
        parsed=False
        try:
            if request.method not in ('GET','HEAD') or request.body:raise ValueError()
            target=urlsplit(request.target)
            if target.fragment or len(target.query)>2048:raise ValueError()
            detail=re.fullmatch('/api/task-archives/([1-9][0-9]{0,18})',target.path)
            identifier=None
            if target.path!='/api/task-archives':
                if not detail or int(detail[1])>=2**63 or target.query:raise ValueError()
                identifier=int(detail[1])
            pairs=parse_qsl(target.query,keep_blank_values=True,strict_parsing=True,max_num_fields=5)
            if len(dict(pairs))!=len(pairs) or set(dict(pairs))-{'page','size','q','state'}:raise ValueError()
            q=dict(pairs);page=1;size=20
            for name in ('page','size'):
                if name in q and not re.fullmatch('-?[0-9]{1,6}',q[name]):raise ValueError()
            page=int(q.get('page','1'));size=int(q.get('size','20'))
            search=q.get('q','').strip();state=q.get('state','').strip()
            if len(search.encode())>512 or '\x00' in search or state and state not in STATES or page>100000:raise ValueError()
            parsed=True;receipt()
            with guard(w):
                receipt();result=ArchiveCatalogReader(e).read(search=search,state=state,page=page,size=size,identifier=identifier);receipt()
                response=error_response(404,'Archive metadata unavailable') if identifier and result is None else json_response(200,result)
                if len(response.body)>2097152:raise RuntimeError()
            receipt()
            return DisclosureResponse(response.status,response.body,response.headers,receipt)
        except PermissionError:return error_response(403,'Archive metadata access denied')
        except Exception:
            if not parsed:return error_response(400,'Invalid archive metadata query')
            try:receipt()
            except Exception:return error_response(403,'Archive metadata access denied')
            return error_response(503,'Archive metadata unavailable')

def build_archive_catalog_application(application,service):
    if type(application)is not WSGIApplication or type(application.router)is not Router or type(service)is not ArchiveCatalogService:
        raise ValueError('Exact original application and archive service required')
    base=application.router;auth=base.authorize;executor=service.executor;pins=service._pins;routes=tuple(base._routes)
    owner=getattr(auth,'__self__',None)
    if type(owner)is not AuthService or getattr(auth,'__func__',None)is not _AUTHORIZED:
        raise ValueError('Original JWT authority required')
    auth_pins=(owner.store,owner.codec,owner.key,owner.clock)
    def overlap(path):
        parts=path.split('/')
        if len(parts) not in (3,4):return False
        for actual,expected in zip(parts,('', 'api','task-archives')):
            pattern=re.escape(actual)
            for name in re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}',actual):
                pattern=pattern.replace(re.escape('{'+name+'}'),r'[^/]+')
            if re.fullmatch(pattern,expected)is None:return False
        return True
    for method,path,*_ in routes:
        if method in ('GET','HEAD') and (overlap(path) or path.startswith('/api/task-archives/')):raise ValueError('Archive read route conflict')
    def check():
        if getattr(owner.authorized,'__self__',None)is not owner or getattr(owner.authorized,'__func__',None)is not _AUTHORIZED or any(a is not b for a,b in zip((owner.store,owner.codec,owner.key,owner.clock),auth_pins)):raise PermissionError()
        if application.router is not base or base.authorize is not auth or tuple(base._routes)!=routes or service.executor is not executor or service._pins is not pins:raise PermissionError()
    def transport(request):
        check()
        if auth(request)is not True:raise PermissionError()
        check();return True
    protected=Router(auth)
    protected.add('GET','/api/task-archives',lambda req,p:service.handle(req,transport,check))
    protected.add('GET','/api/task-archives/{id}',lambda req,p:service.handle(req,transport,check))
    class Overlay:
        def dispatch(self,request):
            try:check()
            except Exception:return error_response(503,'Archive composition unavailable')
            if request.method in ('GET','HEAD') and (request.path=='/api/task-archives' or re.fullmatch(r'/api/task-archives/[^/]+',request.path)):return protected.dispatch(request)
            return base.dispatch(request)
    return WSGIApplication(Overlay(),max_body_bytes=application.max_body_bytes)
