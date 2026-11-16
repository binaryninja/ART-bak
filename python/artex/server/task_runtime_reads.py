"""Explicit fixed-task read composition for the task overview request sequence.

No mutation handlers are added. Original authenticated application remains the
fallback for other paths/methods. Stats preserves upstream GLOBAL asset totals,
so their disclosure requires a distinct stats_global grant. Profile metadata is
public/redacted storage projection, never provider credentials or mutation power.
No implicit active-task selection, model readiness, activity or progress values.
"""
from dataclasses import dataclass
from urllib.parse import parse_qsl,urlsplit
from .core import Router,error_response
from .wsgi import WSGIApplication
from .stats import StatsService
from .exploration import ExplorationService
from .profiles import ProfileService
from ..db.core import Query
from ..db.stats import StatsStore
from ..db.exploration import ExplorationStore
from ..db.profiles import ProfileStore

class _LocalIntentPageStore(ExplorationStore):
    """No inherited expansion, including a relation added after the bounded check."""
    def direct_sources(self):return []

@dataclass(frozen=True)
class TaskReadScope:
    task_id:int
    exploration_id:int
    revision:str

class TaskRuntimeReads:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,runtime_snapshot,
                 findings_service=None,max_response_bytes=2*1024*1024):
        if (type(scope) is not TaskReadScope or any(type(n) is not int or not 0<n<2**63 for n in (scope.task_id,scope.exploration_id))
                or type(scope.revision) is not str or not scope.revision or len(scope.revision.encode())>256):raise ValueError('Explicit bounded task read scope required')
        if any(not callable(f) for f in (authorize,current,lifecycle_guard,runtime_snapshot)):raise ValueError('Explicit read authority and actual runtime snapshot required')
        if type(max_response_bytes) is not int or not 1024<=max_response_bytes<=8*1024*1024:raise ValueError('Bounded read response required')
        self.executor,self.scope=executor,scope
        self._scope_identity=(scope.task_id,scope.exploration_id,scope.revision)
        self.authorize,self.current,self.guard,self.runtime_snapshot=authorize,current,lifecycle_guard,runtime_snapshot
        self._pins=(executor,scope,authorize,current,lifecycle_guard,runtime_snapshot)
        self.findings_service=findings_service;self._findings=findings_service
        self.max_bytes=max_response_bytes
        self.stats=StatsStore(executor,allow_global=True)
        self.graph=ExplorationStore(executor,scope.exploration_id)
        self.profiles=ProfileStore(executor)
        self._stores=(self.stats,self.graph,self.profiles)
    def validate_bindings(self):
        if any(a is not b for a,b in zip((self.executor,self.scope,self.authorize,self.current,self.guard,self.runtime_snapshot),self._pins)):
            raise PermissionError()
        if (tuple((self.scope.task_id,self.scope.exploration_id,self.scope.revision))!=self._scope_identity
                or self.findings_service is not self._findings or (self.stats,self.graph,self.profiles)!=self._stores
                or any(s.executor is not self.executor for s in self._stores)
                or self.graph.exploration_id!=self.scope.exploration_id):raise PermissionError()
        if self.findings_service is not None:
            from .task_findings import TaskFindingsService
            from ..db.task_findings import TaskFindingsStore
            from ..db.task_coverage import TaskCoverageStore
            f=self.findings_service
            if type(f) is not TaskFindingsService or type(f.store) is not TaskFindingsStore or f.store is not f._store:
                raise PermissionError()
            store=f.store;c=store.coverage
            if (type(c) is not TaskCoverageStore or store.executor is not self.executor
                    or c.executor is not self.executor or c._executor is not self.executor
                    or store.scope is not c.scope or c.scope is not c._scope
                    or (c.scope.task_id,c.scope.exploration_id,c.scope.revision)!=self._scope_identity
                    or store.guard is not c.guard
                    or any(a is not b for a,b in zip((c,store.executor,store.scope,store.guard,c.authorize,c.current,store.asset_authorize),store._pins))):
                raise PermissionError()
        return True
    def _check(self):
        self.validate_bindings()
        if self.current(self.scope) is not True:raise PermissionError()
        self.validate_bindings()
    def _allow(self,request,op):
        self._check()
        if self.authorize(request,self.scope,op) is not True:raise PermissionError()
        self._check()
    def bind(self,application):
        """Return a new WSGI view without editing original route declarations."""
        if type(application) is not WSGIApplication:raise ValueError('Actual authenticated WSGI application required')
        if type(application.router) is not Router:raise ValueError('Original authenticated router required')
        self.validate_bindings()
        base=application.router;auth=base.authorize
        routes={'/api/tasks/'+str(self.scope.task_id):'task','/api/stats':'stats',
                '/api/exploration/intents':'intents','/api/llm/profiles':'profiles'}
        if self.findings_service is not None:routes['/api/exploration/findings']='findings'
        protected=Router(auth)
        def checked_read(request,operation):
            # Preserve the original JWT authority through SQL, callbacks and
            # JSON serialization, not merely at initial dispatch.
            if base.authorize is not auth:
                return error_response(503,'Task read authorization changed')
            response=self._read(request,operation,base)
            if base.authorize is not auth:
                return error_response(503,'Task read authorization changed')
            return response
        # Literal declarations keep route auditing exact. The outer dispatcher
        # still selects only this helper's single fixed task path.
        protected.add('GET','/api/tasks/{id}',lambda req,params:checked_read(req,'task'))
        protected.add('GET','/api/stats',lambda req,params:checked_read(req,'stats'))
        protected.add('GET','/api/exploration/intents',lambda req,params:checked_read(req,'intents'))
        protected.add('GET','/api/llm/profiles',lambda req,params:checked_read(req,'profiles'))
        # This inner declaration is unreachable unless findings was added to
        # the exact outer routes map above; absent capability still falls back.
        protected.add('GET','/api/exploration/findings',lambda req,params:checked_read(req,'findings'))
        class ReadRouter:
            def dispatch(inner,request):
                if base.authorize is not auth:return error_response(503,'Task read authorization changed')
                if request.method.upper() in ('GET','HEAD') and request.path in routes:
                    return protected.dispatch(request)
                return base.dispatch(request)
        return WSGIApplication(ReadRouter(),max_body_bytes=application.max_body_bytes)
    def _read(self,request,op,base):
        try:
            if request.body:raise ValueError()
            query=urlsplit(request.target).query
            if len(query)>256:raise ValueError()
            pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=4 if op=='intents' else 2)
            if op=='intents':
                # Frontend intentsPage sends limit/task/before/page. Preserve the
                # fixed task boundary; ExplorationService owns numeric bounds and
                # the legacy-list versus page-envelope selection.
                selectors=dict(pairs)
                if (len(selectors)!=len(pairs) or set(selectors)-{'task','limit','before','page'}
                        or selectors.get('task')!=str(self.scope.task_id)):raise ValueError()
            else:
                expected=[] if op in ('profiles','task') else [('task',str(self.scope.task_id))]
                if pairs!=expected:raise ValueError()
            if op=='findings':
                # The scoped findings service owns its guard and source/asset
                # grants. Never nest our possibly non-reentrant guard around it.
                self._allow(request,op)
                # The outer grant must run inside the canonical final source/asset
                # validation, never after it has released a validated response.
                def disclosure():
                    self._allow(request,op)
                    return True
                response=self.findings_service.handle(request,disclosure=disclosure)
                if len(response.body)>self.max_bytes:return error_response(503,'Task read response exceeds bound')
                self.validate_bindings()
                return response
            with self._pins[4](self.scope):
                self._allow(request,op)
                if op=='stats':self._allow(request,'stats_global')
                if op=='profiles':self._allow(request,'profiles_global')
                if op=='stats':
                    response=StatsService(self.stats,runtime_snapshot=self.runtime_snapshot).get(request)
                elif op=='intents':
                    if ('page' in selectors or 'before' in selectors) and self.executor.fetch_all(Query(
                            'SELECT 1 FROM task_relations WHERE task_id=$1 LIMIT 1',(self.scope.task_id,))):
                        # Generic paging merges inherited tasks. This fixed-task
                        # capability has no authority for those sources.
                        return error_response(503,'Inherited intent paging unavailable')
                    store=_LocalIntentPageStore(self.executor,self.scope.exploration_id) if ('page' in selectors or 'before' in selectors) else self.graph
                    response=ExplorationService(lambda task:store if task==str(self.scope.task_id) else None)._handle('intents',request)
                elif op=='profiles':response=ProfileService(self.profiles)._handle('list',request)
                elif op=='findings':response=self.findings_service.handle(request)
                else:response=base.dispatch(request)
                if len(response.body)>self.max_bytes:return error_response(503,'Task read response exceeds bound')
                self._allow(request,op)
                if op=='stats':self._allow(request,'stats_global')
                if op=='profiles':self._allow(request,'profiles_global')
            self._allow(request,op)
            if op=='stats':self._allow(request,'stats_global')
            if op=='profiles':self._allow(request,'profiles_global')
            return response
        except PermissionError:return error_response(403,'Task read access denied')
        except (ValueError,UnicodeError):return error_response(400,'Invalid fixed task read selector')
        except Exception:return error_response(503,'Task read capability unavailable')
