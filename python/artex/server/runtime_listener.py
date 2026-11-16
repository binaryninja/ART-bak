"""Explicit local development transport, not production deployment.

No construction side effects. Never discovers keys/config, installs schema or
starts providers/pump. Call start_listener and start_pump separately. Resource
callbacks belong to the same exclusive host owner, not arbitrary HTTP clients.
They must be idempotent and bounded: Python cannot preempt blocking owner, SQL or
provider callbacks. The stop timeout bounds cooperative waits, not those calls.
stop_admissions must persistently fence ALL external admission paths, not only
HTTP; cancel_owned must use that host's lifecycle authority. Unresolved shutdown
retains the bundle/pool/owner for explicit reconciliation, never automatic retry.
The HTTP connection cap is separate from task admission capacity. If an explicit
manual-action owner is attached, shutdown seals it before callbacks and waits for
both active outer invocations and unresolved inventory before closing resources.
This does not discover untracked manual calls or resolve quarantined actions.
"""
from dataclasses import dataclass
import math,socket,threading,time
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer,WSGIRequestHandler,make_server
from .task_runtime_setup import TaskRuntimeApplication
from .core import Router
from .wsgi import WSGIApplication
from ..db.pool import ExecutorPool

@dataclass(frozen=True)
class DevListenerConfig:
    port:int=8787
    max_connections:int=8
    socket_timeout:float=10.0
    def validate(self):
        if type(self.port) is not int or not 1<=self.port<=65535 or type(self.max_connections) is not int or not 1<=self.max_connections<=32:
            raise ValueError('Bounded loopback port/connections required')
        if type(self.socket_timeout) not in (int,float) or not math.isfinite(self.socket_timeout) or not .1<=self.socket_timeout<=60:raise ValueError('Bounded socket timeout required')

@dataclass(frozen=True)
class RuntimeResourceOwner:
    pool:ExecutorPool
    stop_admissions:object
    cancel_owned:object
    close_resources:object

@dataclass(frozen=True)
class ListenerStop:
    stopped:bool
    reason:str

class _DiscardErrors:
    def write(self,value):return len(value)
    def flush(self):pass

class QuietRuntimeHandler(WSGIRequestHandler):
    protocol_version='HTTP/1.0'
    def log_message(self,*args):pass
    def log_error(self,*args):pass
    def get_stderr(self):return _DiscardErrors()
    def parse_request(self):
        # BaseHTTPRequestHandler normalizes a leading // target. Validate raw
        # origin form first, before that irreversible normalization.
        parts=self.raw_requestline.rstrip(b'\r\n').split(b' ')
        target=parts[1] if len(parts)==3 else b''
        if not super().parse_request():return False
        if not target.startswith(b'/') or target.startswith(b'//') or b'\\' in target or b'#' in target:
            self.send_error(400,'Invalid request target');return False
        # Inspect original repeated headers BEFORE WSGI combines them.
        invalid=any(len(self.headers.get_all(k,[]))>1 for k in ('Authorization','Content-Length','Host','Cookie','Origin'))
        port=self.server.config.port
        hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        if port==80:hosts.update(('127.0.0.1','localhost'))
        host=self.headers.get('Host','')
        origin=self.headers.get('Origin')
        if host not in hosts or origin is not None and origin not in {'http://'+host}:
            self.send_error(403,'Local origin required');return False
        lengths=self.headers.get_all('Content-Length',[])
        invalid=invalid or bool(self.headers.get_all('Transfer-Encoding',[]))
        if lengths:
            value=lengths[0]
            invalid=invalid or not value.isascii() or not value.isdecimal() or len(value)>12
            if not invalid and int(value)>self.server.body_limit:
                self.send_error(413,'Request body too large');return False
        if invalid or not self.path.startswith('/') or self.path.startswith('//'):
            self.send_error(400,'Invalid request framing');return False
        self.close_connection=True
        return True

class _BoundedServer(ThreadingMixIn,WSGIServer):
    daemon_threads=False
    block_on_close=False
    def configure(self,config,body_limit):
        self.config=config;self.body_limit=body_limit
        self._slots=threading.BoundedSemaphore(config.max_connections)
        self._condition=threading.Condition();self._active=set();self._closing=False
    def process_request(self,request,address):
        with self._condition:
            if self._closing or not self._slots.acquire(blocking=False):
                self.shutdown_request(request);return
            self._active.add(request)
        try:super().process_request(request,address)
        except BaseException:
            with self._condition:self._active.discard(request);self._slots.release();self._condition.notify_all()
            self.shutdown_request(request);raise
    def process_request_thread(self,request,address):
        try:
            request.settimeout(self.config.socket_timeout)
            super().process_request_thread(request,address)
        finally:
            with self._condition:self._active.discard(request);self._slots.release();self._condition.notify_all()
    def handle_error(self,*args):pass
    def quiesce(self,timeout):
        with self._condition:self._closing=True;sockets=tuple(self._active)
        for conn in sockets:
            try:conn.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        with self._condition:return self._condition.wait_for(lambda:not self._active,timeout)

class _AuthenticatedView:
    """Retain the assembly's original JWT authority through every response chunk.

    Uses explicit bootstrap router ownership, never closure introspection. Headers
    are deferred until the first validated chunk; after headers, revocation closes
    the iterable instead of mixing an error body into the original response.
    """
    def __init__(self,app,check):self.app=app;self.check=check
    def __call__(self,environ,start_response):
        def output():
            result=None;started=False;pending=[]
            def capture(status,headers,exc_info=None):
                if pending:raise RuntimeError('Repeated response start')
                pending.append((status,headers))
                # Our exact WSGIApplication never uses the legacy write callable.
                return lambda data:(_ for _ in ()).throw(RuntimeError('Unsupported WSGI write'))
            try:
                self.check();result=self.app(environ,capture);self.check()
                iterator=iter(result)
                while True:
                    self.check()
                    try:chunk=next(iterator)
                    except StopIteration:break
                    self.check()
                    if not started:
                        if len(pending)!=1:raise RuntimeError('Missing response headers')
                        start_response(*pending[0]);started=True
                    self.check()
                    from .disclosure import DisclosureBody
                    if type(result) is DisclosureBody:
                        DisclosureBody.validate_disclosure(result)
                        self.check()
                    yield chunk
                self.check()
                if not started:
                    if len(pending)!=1:raise RuntimeError('Missing response headers')
                    start_response(*pending[0]);started=True
            except Exception:
                if not started:
                    body=b'{"error":"Runtime authentication unavailable"}\n'
                    start_response('503 Service Unavailable',[('Content-Type','application/json'),('Content-Length',str(len(body)))])
                    if environ.get('REQUEST_METHOD')!='HEAD':yield body
            finally:
                if result is not None and callable(getattr(result,'close',None)):result.close()
        return output()

_AUTHENTICATED_CALL=_AuthenticatedView.__call__

class _RuntimeSkillView:
    """Route owned skills without delaying their real post-header fences."""
    def __init__(self,skills,base,pool,runtime_view,check):
        from .skill_read_setup import SkillReadBundle,_SkillReadApplication,_PATTERNS
        if type(skills) is not SkillReadBundle or type(skills.application) is not _SkillReadApplication:
            raise ValueError('Exact skill read composition required')
        self.skills=skills;self.base=base;self.pool=pool
        self.runtime_view=runtime_view;self.check=check
        self.app=skills.application;self.patterns=tuple(_PATTERNS[:len(self.app._views)])
        self.views=tuple(self.app._views)
        self.methods=(_SKILL_APPLICATION_CALL,_SKILL_APPLICATION_CHECK)
        self._fixed=(skills,base,pool,runtime_view,check,self.app,self.patterns,self.views,self.methods)
        self.validate()
    def validate(self):
        if any(a is not b for a,b in zip((self.skills,self.base,self.pool,self.runtime_view,self.check,self.app,self.patterns,self.views,self.methods),self._fixed)):
            raise RuntimeError("Runtime skill adapter changed")
        app=self.app
        if self.skills.application is not app or app.original is not self.base or app._original is not self.base or tuple(app._views)!=self.views:
            raise RuntimeError('Runtime skill ownership changed')
        for name,method in zip(('__call__','_check'),self.methods):
            bound=getattr(app,name)
            if getattr(bound,'__self__',None) is not app or getattr(bound,'__func__',None) is not method:
                raise RuntimeError('Runtime skill implementation changed')
        for index,view in enumerate(self.views):
            if view is not None:app._check(index)
        # Every optional ledger path belongs to this listener's resource owner.
        for index,name in enumerate(("usage","missing","files","content","catalog")):
            view=self.views[index] if index<len(self.views) else None
            service=getattr(self.skills,name)
            if (view is None and service is not None) or (view is not None and getattr(view,name) is not service):
                raise RuntimeError("Runtime skill service binding changed")
        readers=[self.skills.usage.reader,self.skills.missing.reader]
        if self.skills.catalog is not None and self.skills.catalog.reader.usage_reader is not None:
            readers.append(self.skills.catalog.reader.usage_reader)
        if any(reader.executor is not self.pool for reader in readers):
            raise ValueError('Skill ledgers require the exact runtime pool')
    def __call__(self,environ,start_response):
        path=environ.get('PATH_INFO','')
        selected=any(view is not None and type(path) is str and pattern.fullmatch(path)
                     for pattern,view in zip(self.patterns,self.views))
        if not selected:
            # Validate before invoking the fallback: a replaced callable must
            # not bypass the original runtime wrapper and its authentication.
            try:self._fixed[4]()
            except Exception:
                body=b'{"error":"Runtime skill reads unavailable"}\n'
                start_response('503 Service Unavailable',[('Content-Type','application/json'),('Content-Length',str(len(body)))])
                return [] if environ.get('REQUEST_METHOD')=='HEAD' else [body]
            return self._fixed[3](environ,start_response)
        def output():
            result=None;started=False
            def actual(status,headers,exc_info=None):
                nonlocal started
                self._fixed[4]();started=True
                return start_response(status,headers,exc_info) if exc_info is not None else start_response(status,headers)
            try:
                self._fixed[4]()
                # The original composer observes actual start_response mutations
                # before its final JWT/source/snapshot checks and body return.
                result=self.app(environ,actual);self._fixed[4]()
                for chunk in result:
                    self._fixed[4]();yield chunk
            except Exception:
                if not started:
                    body=b'{"error":"Runtime skill reads unavailable"}\n'
                    start_response('503 Service Unavailable',[('Content-Type','application/json'),('Content-Length',str(len(body)))])
                    if environ.get('REQUEST_METHOD')!='HEAD':yield body
            finally:
                if result is not None and callable(getattr(result,'close',None)):result.close()
        return output()

# Capture trusted composition methods at import, never a pre-bind replacement.
from .skill_read_setup import _SkillReadApplication
_SKILL_APPLICATION_CALL=_SkillReadApplication.__call__
_SKILL_APPLICATION_CHECK=_SkillReadApplication._check
_RUNTIME_SKILL_METHODS=(_RuntimeSkillView.__init__,_RuntimeSkillView.__call__,_RuntimeSkillView.validate)


from ..agent.task_continuation import TaskContinuationOwner
_CONTINUATION_NAMES=('seal_resume','wait_resumes','validate_bindings')
_CONTINUATION_METHODS=tuple(getattr(TaskContinuationOwner,n) for n in _CONTINUATION_NAMES)


from .archive_runtime_view import RuntimeArchiveView
_ARCHIVE_METHODS=(RuntimeArchiveView.__init__,RuntimeArchiveView.__call__,RuntimeArchiveView.validate)

class RuntimeListener:
    def __init__(self,bundle,owner,config=DevListenerConfig(),*,skill_reads=None,archive_catalog=None):
        if type(bundle) is not TaskRuntimeApplication or type(owner) is not RuntimeResourceOwner or type(owner.pool) is not ExecutorPool or type(config) is not DevListenerConfig:
            raise ValueError('Actual runtime, pool ownership and development configuration required')
        config.validate()
        if any(not callable(f) for f in (owner.stop_admissions,owner.cancel_owned,owner.close_resources)):raise ValueError('Explicit resource shutdown ownership required')
        if bundle.runtime.executor is not owner.pool:raise ValueError('Runtime must use exact shared executor pool')
        bundle._check()
        base=bundle.bootstrap.app
        if type(base) is not WSGIApplication or type(base.router) is not Router or not callable(base.router.authorize) or type(bundle.app) is not WSGIApplication:
            raise ValueError('Code-owned bootstrap JWT application required')
        self._auth_pins=(base,base.router,base.router.authorize,bundle.app.router)
        self._bundle=bundle;self._owner=owner;self._config=config;self._original_config=config
        self._pins=(bundle.runtime,bundle.app,bundle.pump,bundle.host,bundle.manager,owner.pool,owner.stop_admissions,owner.cancel_owned,owner.close_resources)
        self._continuation=bundle.runner._continuation_owner
        if self._continuation is not None:
            if type(self._continuation) is not TaskContinuationOwner or self._continuation.application is not bundle or bundle.manager._continuation_owner is not self._continuation:
                raise ValueError('Exact installed continuation owner required')
            self._check_continuation()
        self._manual=bundle.manual_actions
        self._manual_names=('validate_bindings','stop_new_actions','cancel_active','wait','snapshot')
        self._manual_methods=None
        if self._manual is not None:
            methods=tuple(getattr(self._manual,n) for n in self._manual_names)
            if any(getattr(m,'__self__',None) is not self._manual or getattr(m,'__func__',None) is not getattr(type(self._manual),n) for n,m in zip(self._manual_names,methods)):
                raise ValueError('Original manual action operations required')
            self._manual_methods=tuple((m.__self__,m.__func__) for m in methods)
        self._view=_AuthenticatedView(bundle.app,self._check)
        self._skill_view=None
        if skill_reads is not None:
            if any(a is not b for a,b in zip((_RuntimeSkillView.__init__,_RuntimeSkillView.__call__,_RuntimeSkillView.validate),_RUNTIME_SKILL_METHODS)):
                raise ValueError("Original runtime skill adapter required")
            self._skill_view=_RuntimeSkillView(skill_reads,base,owner.pool,self._view,self._check)
            self._view=self._skill_view
        self._skill_binding=self._skill_view
        self._archive_view=None
        if archive_catalog is not None:
            if any(a is not b for a,b in zip((RuntimeArchiveView.__init__,RuntimeArchiveView.__call__,RuntimeArchiveView.validate),_ARCHIVE_METHODS)):
                raise ValueError('Original archive runtime adapter required')
            self._archive_view=RuntimeArchiveView(archive_catalog,base,owner.pool,self._view,self._check,bundle.app)
            self._view=self._archive_view
        self._archive_binding=self._archive_view
        self._lock=threading.RLock();self._server=None;self._thread=None;self._stopping=False;self._closed=False;self._resources_closed=False;self._shutdown_active=False
    def _check_continuation(self):
        owner=self._continuation
        if self._bundle.runner._continuation_owner is not owner or self._bundle.manager._continuation_owner is not owner:
            raise RuntimeError('Continuation shutdown owner changed')
        if owner is not None:
            for name,method in zip(_CONTINUATION_NAMES,_CONTINUATION_METHODS):
                bound=getattr(owner,name)
                if getattr(bound,'__self__',None) is not owner or getattr(bound,'__func__',None) is not method:
                    raise RuntimeError('Continuation shutdown method changed')
            owner.validate_bindings()
    def _check(self):
        b,o=self._bundle,self._owner
        self._check_continuation()
        if b.manual_actions is not self._manual:raise RuntimeError('Manual action owner changed')
        if self._manual is not None:
            for name,(owner,method) in zip(self._manual_names,self._manual_methods):
                current=getattr(self._manual,name)
                if getattr(current,'__self__',None) is not owner or getattr(current,'__func__',None) is not method:
                    raise RuntimeError('Manual action operation changed')
        if any(a is not v for a,v in zip((b.runtime,b.app,b.pump,b.host,b.manager,o.pool,o.stop_admissions,o.cancel_owned,o.close_resources),self._pins)) or b.runtime.executor is not o.pool:
            raise RuntimeError('Runtime listener ownership changed')
        base,router,auth,outer=self._auth_pins
        if b.bootstrap.app is not base or base.router is not router or router.authorize is not auth or b.app.router is not outer:
            raise RuntimeError('Original runtime authentication changed')
        if self._config is not self._original_config:raise RuntimeError('Listener configuration changed')
        b._check();self._config.validate()
        if self._skill_view is not self._skill_binding:raise RuntimeError('Runtime skill adapter ownership changed')
        if self._skill_view is not None:
            if (self._archive_view.fallback if self._archive_view is not None else self._view) is not self._skill_view:raise RuntimeError('Runtime skill transport changed')
            for name,method in zip(('__init__','__call__','validate'),_RUNTIME_SKILL_METHODS):
                bound=getattr(self._skill_view,name)
                if getattr(bound,'__self__',None) is not self._skill_view or getattr(bound,'__func__',None) is not method:
                    raise RuntimeError('Runtime skill adapter implementation changed')
            self._skill_view.validate()
        if self._archive_view is not self._archive_binding:raise RuntimeError('Runtime archive adapter ownership changed')
        if self._archive_view is not None:
            if self._view is not self._archive_view:raise RuntimeError('Runtime archive transport changed')
            for name,method in zip(('__init__','__call__','validate'),_ARCHIVE_METHODS):
                bound=getattr(self._archive_view,name)
                if getattr(bound,'__self__',None)is not self._archive_view or getattr(bound,'__func__',None)is not method:
                    raise RuntimeError('Runtime archive implementation changed')
            self._archive_view.validate()
    def start_listener(self):
        with self._lock:
            self._check()
            if self._closed or self._stopping:raise RuntimeError('Listener shutdown already requested')
            if self._server is not None:return False
            server=make_server('127.0.0.1',self._config.port,self._view,server_class=_BoundedServer,handler_class=QuietRuntimeHandler)
            try:server.configure(self._config,self._pins[1].max_body_bytes)
            except BaseException:server.server_close();raise
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},name='artex-loopback',daemon=False)
            self._server=server;self._thread=thread
            try:thread.start()
            except BaseException:self._server=None;self._thread=None;server.server_close();raise
            return True
    def start_pump(self):
        with self._lock:
            self._check()
            if self._stopping or self._closed:raise RuntimeError('Listener shutdown already requested')
            return self._bundle.start_pump()
    def stop(self,timeout):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0:raise ValueError('Finite shutdown timeout required')
        deadline=time.monotonic()+timeout
        remaining=lambda:max(0,deadline-time.monotonic())
        with self._lock:
            if self._shutdown_active:return ListenerStop(False,'shutdown_busy')
            if self._closed:return ListenerStop(True,'already_closed')
            self._stopping=True
            try:self._check()
            except Exception:return ListenerStop(False,'ownership_changed')
            self._shutdown_active=True
        # External shutdown/cancellation callbacks and cooperative waits must
        # never run under the listener lock. Concurrent/reentrant stops observe
        # shutdown_busy, and retained manual invocations can finish independently.
        try:
            if self._continuation is not None:
                if self._continuation.seal_resume() is not True:return ListenerStop(False,'resume_admission_unresolved')
                self._check()
            if self._manual is not None:
                if self._manual.stop_new_actions() is not True:return ListenerStop(False,'manual_admission_unresolved')
                self._check()
            if self._pins[6]() is not True:return ListenerStop(False,'admission_unresolved')
            self._check()
            if self._server is not None:
                self._server.shutdown();self._server.server_close()
                if not self._server.quiesce(remaining()):return ListenerStop(False,'http_unresolved')
                self._thread.join(remaining())
                if self._thread.is_alive():return ListenerStop(False,'listener_unresolved')
            if self._bundle.stop_pump(remaining()) is not True:return ListenerStop(False,'pump_unresolved')
            if self._manual is not None:
                self._manual.cancel_active();self._check()
            if self._pins[7]() is not True:return ListenerStop(False,'cancellation_unresolved')
            self._check()
            drain=self._bundle.host.drain(remaining())
            if drain.all_settled is not True:return ListenerStop(False,'runtime_unresolved')
            self._check()
            # A retired manager owner is not enough: the outer manual invocation
            # may still be unwinding after its terminal SQL commit.
            if self._manual is not None:
                if self._manual.wait(remaining()).drained is not True:return ListenerStop(False,'manual_actions_unresolved')
                self._check()
            if self._continuation is not None:
                if self._continuation.wait_resumes(remaining()) is not True:return ListenerStop(False,'resume_actions_unresolved')
                self._check()
            if not self._resources_closed:
                if self._pins[8]() is not True:return ListenerStop(False,'resources_unresolved')
                self._resources_closed=True
                self._check()
            if self._owner.pool.close(timeout=remaining()).drained is not True:return ListenerStop(False,'pool_unresolved')
            with self._lock:self._closed=True
            return ListenerStop(True,'closed')
        except BaseException:return ListenerStop(False,'shutdown_unresolved')
        finally:
            with self._lock:self._shutdown_active=False
