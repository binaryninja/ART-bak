"""Owned archive read dispatch, no resource acquisition or default mounting."""
import re
from .archive_catalog import ArchiveCatalogService,build_archive_catalog_application
from .wsgi import WSGIApplication

_HANDLE=ArchiveCatalogService.handle
_BUILD=build_archive_catalog_application
_WSGI=(WSGIApplication.__call__,WSGIApplication._handle)

def _method(obj,name,expected):
    method=getattr(obj,name)
    return getattr(method,'__self__',None)is obj and getattr(method,'__func__',None)is expected

class RuntimeArchiveView:
    def __init__(self,service,base,pool,fallback,check,runtime_app):
        if type(service)is not ArchiveCatalogService or service.executor is not pool or not _method(service,'handle',_HANDLE):
            raise ValueError('Original archive service and exact runtime pool required')
        from .runtime_listener import _AuthenticatedView,_AUTHENTICATED_CALL,_RuntimeSkillView
        if _AuthenticatedView.__call__ is not _AUTHENTICATED_CALL:raise ValueError('Original authenticated transport required')
        leaf=fallback.runtime_view if type(fallback)is _RuntimeSkillView else fallback
        if type(leaf)is not _AuthenticatedView or leaf.app is not runtime_app or not _method(leaf,'__call__',_AUTHENTICATED_CALL):
            raise ValueError('Original runtime fallback transport required')
        if getattr(leaf.check,'__self__',None)is not getattr(check,'__self__',None) or getattr(leaf.check,'__func__',None)is not getattr(check,'__func__',None):
            raise ValueError('Original runtime fallback owner required')
        router=runtime_app.router;dispatch=router.dispatch
        if type(runtime_app)is not WSGIApplication or any(not _method(runtime_app,n,m) for n,m in zip(('__call__','_handle'),_WSGI)) or getattr(dispatch,'__self__',None)is not router or getattr(dispatch,'__func__',None)is not getattr(type(router),'dispatch',None):
            raise ValueError('Original runtime fallback implementation required')
        self._runtime_router_pins=(router,getattr(dispatch,'__func__',None))
        self._fallback_pins=(leaf,runtime_app,leaf.check)
        self.service=service;self.base=base;self.pool=pool;self.fallback=fallback;self.check=check
        self.app=_BUILD(base,service)
        self.router=self.app.router;self.dispatch=self.router.dispatch
        self.view=_AuthenticatedView(self.app,check)
        self._fixed=(service,base,pool,fallback,check,self.app,self.router,self.dispatch,self.view,service._pins)
        self.validate()
    def validate(self):
        from .runtime_listener import _AUTHENTICATED_CALL
        if not _method(self.view,'__call__',_AUTHENTICATED_CALL):raise RuntimeError('Authenticated transport changed')
        leaf,runtime_app,check=self._fallback_pins
        from .runtime_listener import _RuntimeSkillView
        if (self.fallback.runtime_view if type(self.fallback)is _RuntimeSkillView else self.fallback)is not leaf or leaf.app is not runtime_app or leaf.check is not check or not _method(leaf,'__call__',_AUTHENTICATED_CALL):
            raise RuntimeError('Runtime fallback transport changed')
        router,dispatch=self._runtime_router_pins
        if runtime_app.router is not router or not _method(router,'dispatch',dispatch) or any(not _method(runtime_app,n,m) for n,m in zip(('__call__','_handle'),_WSGI)):
            raise RuntimeError('Runtime fallback implementation changed')
        if any(a is not b for a,b in zip((self.service,self.base,self.pool,self.fallback,self.check,self.app,self.router,self.dispatch,self.view,self.service._pins),self._fixed)):
            raise RuntimeError('Archive runtime binding changed')
        if self.service.executor is not self.pool or not _method(self.service,'handle',_HANDLE) or self.app.router is not self.router:
            raise RuntimeError('Archive runtime ownership changed')
        current=self.router.dispatch
        if getattr(current,'__self__',None)is not getattr(self.dispatch,'__self__',None) or getattr(current,'__func__',None)is not getattr(self.dispatch,'__func__',None):
            raise RuntimeError('Archive dispatch changed')
        if any(not _method(self.app,n,m) for n,m in zip(('__call__','_handle'),_WSGI)) or self.view.app is not self.app or self.view.check is not self.check:
            raise RuntimeError('Archive transport changed')
    def __call__(self,environ,start_response):
        try:self._fixed[4]()
        except Exception:
            body=b'{"error":"Runtime archive reads unavailable"}\n'
            start_response('503 Service Unavailable',[('Content-Type','application/json'),('Content-Length',str(len(body)))])
            return [] if environ.get('REQUEST_METHOD')=='HEAD' else [body]
        path=environ.get('PATH_INFO','')
        selected=environ.get('REQUEST_METHOD') in ('GET','HEAD') and type(path)is str and (path=='/api/task-archives' or re.fullmatch(r'/api/task-archives/[^/]+',path))
        return (self._fixed[8] if selected else self._fixed[3])(environ,start_response)
