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
from .task_goal_reads import TaskGoalReads
from .task_coverage_reads import TaskCoverageReads
from .task_intent_assets import TaskIntentAssetsService
from .token_usage import TokenUsageService
from .task_model_usage_read import TaskModelUsageRead
from .task_scope_read import TaskScopeRead
from .task_constraint_read import TaskConstraintRead
from .task_intercept_rule_read import TaskInterceptRuleRead
from .task_intercept_history_read import TaskInterceptHistoryRead
from ..db.token_usage import TokenUsageStore,UsageScope
from ..db.task_intent_assets import TaskIntentAssetsStore
from ..db.main_sessions import MainSessionsStore
from ..db.task_coverage import TaskCoverageStore,CoverageScope
from ..db.task_findings import TaskFindingsStore
from ..db.task_activity_read import TaskActivityReadStore,ActivityReadScope

@dataclass(frozen=True)
class TaskReadServices:
    reads:TaskRuntimeReads
    activity:TaskActivityService
    main_sessions:MainSessionsService | None = None
    goals:TaskGoalReads | None = None
    coverage:TaskCoverageReads | None = None
    intent_assets:TaskIntentAssetsService | None = None
    token_usage:TokenUsageService | None = None
    model_usage:TaskModelUsageRead | None = None
    scope:TaskScopeRead | None = None
    constraints:TaskConstraintRead | None = None
    intercept_rules:TaskInterceptRuleRead | None = None
    intercept_history:TaskInterceptHistoryRead | None = None

class TaskReadBuilder:
    def __init__(self,executor,*,request_authorize,context_authorize,asset_authorize,
                 runtime_snapshot,activity_authorize,activity_raw_authorize,
                 activity_request_authorize,stream_authorize,stream_wait=None,
                 max_rows=500,max_bytes=1048576,max_batches=20,max_polls=3,
                 main_sessions_authorize=None,main_sessions_request_authorize=None,
                 goal_authorize=None,goal_request_authorize=None,
                 coverage_authorize=None,coverage_request_authorize=None,coverage_asset_authorize=None,coverage_connector_authorize=None,
                 intent_assets_authorize=None,intent_assets_request_authorize=None,
                 token_usage_workspace=None,token_usage_authorize=None,token_usage_request_authorize=None,
                 model_usage_authorize=None,model_usage_request_authorize=None,
                 scope_authorize=None,scope_request_authorize=None,
                 constraint_authorize=None,constraint_request_authorize=None,
                 intercept_rule_workspace=None,intercept_rule_authorize=None,intercept_rule_request_authorize=None,
                 intercept_history_workspace=None,intercept_history_authorize=None,intercept_history_raw_authorize=None,intercept_history_request_authorize=None):
        policies=(request_authorize,context_authorize,asset_authorize,runtime_snapshot,
                  activity_authorize,activity_raw_authorize,activity_request_authorize,stream_authorize,stream_wait)
        if any(not callable(f) for f in policies[:-1]) or stream_wait is not None and not callable(stream_wait):raise ValueError('Explicit independent task read grants required')
        if (type(max_rows) is not int or not 1<=max_rows<=500 or type(max_bytes) is not int or not 1024<=max_bytes<=1048576
                or type(max_batches) is not int or not 1<=max_batches<=100 or type(max_polls) is not int or not 1<=max_polls<=100):raise ValueError('Bounded read configuration required')
        if (main_sessions_authorize is None)!=(main_sessions_request_authorize is None) or any(f is not None and not callable(f) for f in (main_sessions_authorize,main_sessions_request_authorize)):
            raise ValueError('Both independent main-session grants required')
        if (goal_authorize is None)!=(goal_request_authorize is None) or any(f is not None and not callable(f) for f in (goal_authorize,goal_request_authorize)):
            raise ValueError('Both independent goal read grants required')
        coverage_policies=(coverage_authorize,coverage_request_authorize,coverage_asset_authorize,coverage_connector_authorize)
        if any(f is not None for f in coverage_policies) and (any(not callable(f) for f in coverage_policies[:3]) or coverage_connector_authorize is not None and not callable(coverage_connector_authorize)):
            raise ValueError('Independent coverage source/request/asset grants required')
        intent_policies=(intent_assets_authorize,intent_assets_request_authorize)
        if (intent_assets_authorize is None)!=(intent_assets_request_authorize is None) or any(f is not None and not callable(f) for f in intent_policies):
            raise ValueError('Independent intent-asset source and request grants required')
        self._intent_asset_policies=intent_policies;self._original_intent_asset_policies=intent_policies
        token_policies=(token_usage_workspace,token_usage_authorize,token_usage_request_authorize)
        if any(v is not None for v in token_policies) and (type(token_usage_workspace) is not str
                or not 1<=len(token_usage_workspace.encode())<=256 or not callable(token_usage_authorize)
                or not callable(token_usage_request_authorize)):
            raise ValueError('Explicit token workspace and independent source/request grants required')
        self._token_policies=token_policies;self._original_token_policies=token_policies
        model_policies=(model_usage_authorize,model_usage_request_authorize)
        if (model_usage_authorize is None)!=(model_usage_request_authorize is None) or any(f is not None and not callable(f) for f in model_policies):
            raise ValueError('Independent by-model source/request grants required')
        self._model_policies=model_policies;self._original_model_policies=model_policies
        scope_policies=(scope_authorize,scope_request_authorize)
        if (scope_authorize is None)!=(scope_request_authorize is None) or any(f is not None and not callable(f) for f in scope_policies):
            raise ValueError('Independent scope source/request grants required')
        self._scope_policies=scope_policies;self._original_scope_policies=scope_policies
        constraint_policies=(constraint_authorize,constraint_request_authorize)
        if (constraint_authorize is None)!=(constraint_request_authorize is None) or any(f is not None and not callable(f) for f in constraint_policies):
            raise ValueError('Independent constraint source/request grants required')
        self._constraint_policies=constraint_policies;self._original_constraint_policies=constraint_policies
        intercept_rule_policies=(intercept_rule_workspace,intercept_rule_authorize,intercept_rule_request_authorize)
        if any(v is not None for v in intercept_rule_policies) and (type(intercept_rule_workspace) is not str
                or not 1<=len(intercept_rule_workspace.encode())<=256 or not callable(intercept_rule_authorize)
                or not callable(intercept_rule_request_authorize)):
            raise ValueError('Explicit rule workspace and independent grants required')
        self._intercept_rule_policies=intercept_rule_policies;self._original_intercept_rule_policies=intercept_rule_policies
        history_policies=(intercept_history_workspace,intercept_history_authorize,intercept_history_raw_authorize,intercept_history_request_authorize)
        if any(v is not None for v in history_policies) and (type(intercept_history_workspace) is not str
                or not 1<=len(intercept_history_workspace.encode())<=256 or any(not callable(f) for f in history_policies[1:])):
            raise ValueError('Explicit history workspace and metadata/raw/request grants required')
        self._history_policies=history_policies;self._original_history_policies=history_policies
        self._coverage_policies=coverage_policies;self._original_coverage_policies=coverage_policies
        self._goal_policies=(goal_authorize,goal_request_authorize)
        self._original_goal_policies=self._goal_policies
        self._session_policies=(main_sessions_authorize,main_sessions_request_authorize)
        self._original_session_policies=self._session_policies
        self.executor=executor;self._executor=executor;self._policies=policies
        self._bounds=(max_rows,max_bytes,max_batches,max_polls)
        self._original_policies=policies;self._original_bounds=self._bounds
    def validate_bindings(self):
        if (self.executor is not self._executor or self._policies is not self._original_policies
                or self._bounds is not self._original_bounds or self._session_policies is not self._original_session_policies or self._goal_policies is not self._original_goal_policies or self._coverage_policies is not self._original_coverage_policies or self._intent_asset_policies is not self._original_intent_asset_policies or self._token_policies is not self._original_token_policies or self._model_policies is not self._original_model_policies or self._scope_policies is not self._original_scope_policies or self._constraint_policies is not self._original_constraint_policies or self._intercept_rule_policies is not self._original_intercept_rule_policies or self._history_policies is not self._original_history_policies):raise ValueError('Read builder configuration changed')
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
        goals=None
        goal_read,goal_request=self._goal_policies
        if goal_read is not None:
            goals=TaskGoalReads(self.executor,scope,authorize=lambda s:grant(goal_read,scope),
                request_authorize=lambda req,s:grant(goal_request,req,scope),current=live,lifecycle_guard=guard)
        coverage_reads=None
        csource,crequest,casset,cconnector=self._coverage_policies
        if csource is not None:
            coverage_reads=TaskCoverageReads(self.executor,scope,authorize=lambda s,t,e:grant(csource,scope,t,e),
                request_authorize=lambda req,s,selection:grant(crequest,req,scope,selection),
                asset_authorize=lambda s,selection,ids:grant(casset,scope,selection,ids),
                connector_authorize=None if cconnector is None else lambda s,k,v:grant(cconnector,scope,k,v),
                current=live,lifecycle_guard=guard,max_rows=rows,max_bytes=bytes_)
        intent_assets=None
        isource,irequest=self._intent_asset_policies
        if isource is not None:
            # Independent raw anchor-metadata authority, not implicit reuse of
            # findings, coverage, generic task or execution grants.
            icontext=TaskCoverageStore(self.executor,CoverageScope(*identity),
                authorize=lambda s,t,e:grant(isource,scope,t,e),current=live,lifecycle_guard=guard,
                max_rows=rows,max_bytes=bytes_)
            intent_assets=TaskIntentAssetsService(TaskIntentAssetsStore(icontext),
                authorize=lambda req,s:grant(irequest,req,scope))
        token_usage=None
        workspace,tsource,trequest=self._token_policies
        if workspace is not None:
            usage_scope=UsageScope(workspace,scope.task_id,scope.exploration_id)
            def usage_exact(candidate):
                exact(scope)
                if candidate is not usage_scope or (candidate.workspace,candidate.task_id,candidate.exploration_id)!=(workspace,*identity[:2]):
                    raise PermissionError('Token scope changed')
            def usage_current(candidate):
                usage_exact(candidate);result=live(scope);usage_exact(candidate);return result
            def usage_guard(candidate):
                usage_exact(candidate);return guard(scope)
            def usage_authorize(candidate,operation):
                usage_exact(candidate);result=grant(tsource,scope,operation);usage_exact(candidate);return result
            def usage_request(req,candidate,operation):
                usage_exact(candidate);result=grant(trequest,req,scope,operation);usage_exact(candidate);return result
            token_usage=TokenUsageService(TokenUsageStore(self.executor,usage_scope,
                authorize=usage_authorize,current=usage_current,lifecycle_guard=usage_guard,max_rows=rows,max_bytes=bytes_),
                authorize=usage_request,max_response_bytes=bytes_)
        model_usage=None
        msource,mrequest=self._model_policies
        if msource is not None:
            model_usage=TaskModelUsageRead(self.executor,scope,authorize=lambda s:grant(msource,scope),
                request_authorize=lambda req,s:grant(mrequest,req,scope),current=live,lifecycle_guard=guard,
                max_rows=rows,max_bytes=bytes_)
        scope_read=None
        ssource,srequest=self._scope_policies
        if ssource is not None:
            scope_read=TaskScopeRead(self.executor,scope,authorize=lambda s:grant(ssource,scope),
                request_authorize=lambda req,s:grant(srequest,req,scope),current=live,lifecycle_guard=guard,
                max_rows=rows,max_bytes=bytes_)
        constraint_read=None
        constraint_source,constraint_request=self._constraint_policies
        if constraint_source is not None:
            constraint_read=TaskConstraintRead(self.executor,scope,authorize=lambda s:grant(constraint_source,scope),
                request_authorize=lambda req,s:grant(constraint_request,req,scope),current=live,lifecycle_guard=guard,
                max_rows=rows,max_bytes=bytes_)
        intercept_rule_read=None
        rule_workspace,rule_source,rule_request=self._intercept_rule_policies
        if rule_workspace is not None:
            intercept_rule_read=TaskInterceptRuleRead(self.executor,scope,workspace=rule_workspace,
                authorize=lambda s:grant(rule_source,scope),request_authorize=lambda req,s:grant(rule_request,req,scope),
                current=live,lifecycle_guard=guard,max_rows=rows,max_bytes=bytes_)
        history_read=None
        history_workspace,history_source,history_raw,history_request=self._history_policies
        if history_workspace is not None:
            history_read=TaskInterceptHistoryRead(self.executor,scope,workspace=history_workspace,
                authorize=lambda s,selection:grant(history_source,scope,selection),
                raw_authorize=lambda s,selection:grant(history_raw,scope,selection),
                request_authorize=lambda req,s,selection:grant(history_request,req,scope,selection),
                current=live,lifecycle_guard=guard,max_rows=rows,max_bytes=bytes_)
        return TaskReadServices(reads,activity_service,sessions,goals,coverage_reads,intent_assets,token_usage,model_usage,scope_read,constraint_read,intercept_rule_read,history_read)
