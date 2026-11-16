"""Code-owned read components built only after an authorized task resolution.

No implicit grants, task lookup, global fallback, active selection or runtime
progress inference. The router supplies a frozen resolved scope and fresh request
lifetime check. This constructor/build perform no SQL, policy calls or streams.
"""
from dataclasses import dataclass
from .task_runtime_reads import TaskRuntimeReads,TaskReadScope
from .task_findings import TaskFindingsService
from .task_activity import TaskActivityService
from .main_sessions import MainSessionsService
from ..db.main_sessions import MainSessionsStore
from ..db.task_coverage import TaskCoverageStore,CoverageScope
from ..db.task_findings import TaskFindingsStore
from ..db.task_activity_read import TaskActivityReadStore,ActivityReadScope

@dataclass(frozen=True)
class TaskReadServices:
    reads:TaskRuntimeReads
    activity:TaskActivityService
    main_sessions:MainSessionsService | None = None

class TaskReadBuilder:
    def __init__(self,executor,*,request_authorize,context_authorize,asset_authorize,
                 runtime_snapshot,activity_authorize,activity_raw_authorize,
                 activity_request_authorize,stream_authorize,stream_wait=None,
                 max_rows=500,max_bytes=1048576,max_batches=20,max_polls=3,
                 main_sessions_authorize=None,main_sessions_request_authorize=None):
        policies=(request_authorize,context_authorize,asset_authorize,runtime_snapshot,
                  activity_authorize,activity_raw_authorize,activity_request_authorize,stream_authorize,stream_wait)
        if any(not callable(f) for f in policies[:-1]) or stream_wait is not None and not callable(stream_wait):raise ValueError('Explicit independent task read grants required')
        if (type(max_rows) is not int or not 1<=max_rows<=500 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576
                or type(max_batches) is not int or not 1<=max_batches<=100 or type(max_polls) is not int or not 1<=max_polls<=100):raise ValueError('Bounded read configuration required')
        if (main_sessions_authorize is None)!=(main_sessions_request_authorize is None) or any(f is not None and not callable(f) for f in (main_sessions_authorize,main_sessions_request_authorize)):
            raise ValueError('Both independent main-session grants required')
        self._session_policies=(main_sessions_authorize,main_sessions_request_authorize)
        self._original_session_policies=self._session_policies
        self.executor=executor;self._executor=executor;self._policies=policies
        self._bounds=(max_rows,max_bytes,max_batches,max_polls)
        self._original_policies=policies;self._original_bounds=self._bounds
    def validate_bindings(self):
        if (self.executor is not self._executor or self._policies is not self._original_policies
                or self._bounds is not self._original_bounds or self._session_policies is not self._original_session_policies):raise ValueError('Read builder configuration changed')
        return True
    def build(self,scope,*,current,lifecycle_guard):
        self.validate_bindings()
        if type(scope) is not TaskReadScope or not callable(current) or not callable(lifecycle_guard):raise ValueError('Resolved scope and lifetime authority required')
        identity=(scope.task_id,scope.exploration_id,scope.revision)
        request,context,asset,snapshot,activity,raw,activity_request,stream,wait=self._policies
        rows,bytes_,batches,polls=self._bounds
        def exact(other):
            self.validate_bindings()
            if (scope.task_id,scope.exploration_id,scope.revision)!=identity or (other.task_id,other.exploration_id,other.revision)!=identity:raise PermissionError('Task read scope changed')
        def live(other):
            exact(other);result=current(scope);exact(other);return result is True
        def guard(other):
            exact(other);return lifecycle_guard(scope)
        def grant(fn,*args):
            exact(scope);result=fn(*args);exact(scope);return result is True
        coverage=TaskCoverageStore(self.executor,CoverageScope(*identity),
            authorize=lambda s,t,e:grant(context,scope,t,e),current=live,lifecycle_guard=guard,
            max_rows=rows,max_bytes=bytes_)
        findings=TaskFindingsService(TaskFindingsStore(coverage,asset_authorize=lambda s,ids:grant(asset,scope,ids)),
            authorize=lambda req,s:grant(request,req,scope,'findings'))
        def runtime(selector):
            exact(scope);value=snapshot(scope,selector);exact(scope);return value
        reads=TaskRuntimeReads(self.executor,scope,authorize=lambda req,s,op:grant(request,req,scope,op),
            current=live,lifecycle_guard=guard,runtime_snapshot=runtime,findings_service=findings,max_response_bytes=bytes_)
        activity_store=TaskActivityReadStore(self.executor,ActivityReadScope(*identity),current=live,lifecycle_guard=guard,
            authorize=lambda s,selection:grant(activity,scope,selection),
            raw_authorize=lambda s,selection:grant(raw,scope,selection),max_rows=rows,max_bytes=bytes_)
        activity_service=TaskActivityService(activity_store,
            authorize=lambda req,s,selection:grant(activity_request,req,scope,selection),
            stream_current=lambda req,s:live(scope) and grant(stream,req,scope),stream_wait=wait,
            max_batches=batches,max_polls=polls)
        reads.validate_bindings()
        sessions=None
        session_read,session_request=self._session_policies
        if session_read is not None:
            session_store=MainSessionsStore(self.executor,CoverageScope(*identity),
                authorize=lambda s:grant(session_read,scope),current=live,lifecycle_guard=guard,max_rows=rows)
            sessions=MainSessionsService(session_store,authorize=lambda req,s:grant(session_request,req,scope),max_response_bytes=bytes_)
        return TaskReadServices(reads,activity_service,sessions)
