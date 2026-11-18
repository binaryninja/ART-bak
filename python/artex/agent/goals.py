"""Explicit bounded goals-only decomposition for an existing task graph.

Source agent/goals.go + tools.go set_goals: text/vulnclass, origin=goals,
open goal nodes, root spawns lineage, exp<ID>-goals session, eight-turn ceiling.
Only structured set_goals calls create nodes; prose is never parsed as success.
Stricter differences: bounded strict arguments, atomic batches and mandatory root
lineage (Go permits per-item failure and ignores link errors).

NOT a complete bootstrap: no origin/exploration creation, automatic constraint extraction,
automatic asset-scope extraction, fallback splitting, activity projection, revival or task
completion. Host validate_input MUST confirm explicit constraints/asset scope are
already handled, or delegated to the explicitly supplied reviewed ConstraintTools
adapter plus its native goals-bound catalog. Default remains goals-only. Supply a
matching reviewed system prompt; GOALS_DEFAULT is appropriate only with constraints
wired. TaskScopeTools may be supplied explicitly with its native goals-bound catalog.
Host-selected prompt order is constraints/scope before goals; tool exposure itself
does not approve model-proposed scope. Per-entry scope commits are not atomic with
goal/constraint batches or the transcript. validate_input remains mandatory. Both adapters must share the
exact executor and guard; callbacks/policy changes require a host revision bump.
Catalog/schema/description/binding metadata enters the durable receipt digest.
The new optional scope identity fields intentionally reject older receipts without
them; no automatic receipt migration or effect replay is performed. No implicit
source permission bypass. Do not enable task creation until the host bridges
persisted run identity and policy revision. Callback changes require revision bump.
Durable scoped JSONL admission prevents ambiguous retries; SQL effects and JSONL
commit are not atomic. Pending receipts block restart rather than replay effects.
"""
from dataclasses import dataclass,asdict,replace
from copy import deepcopy
import hashlib,json
from .chat import ChatAgent,ChatUnavailable
from .runtime import Tool,Limits,RunStopped
from ..db.core import Query,int64
from ..db.exploration import ExplorationStore
from ..sidequestion import Model
from ..tools.constraints import ConstraintTools
from ..tools.task_scope import TaskScopeTools
from ..tools.registry import ToolRegistry
from ..db.tool_catalog import CatalogTool

@dataclass(frozen=True)
class GoalScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    model:Model
    system:str
    @property
    def session_id(self):return f'exp{self.exploration_id}-goals'

_GOAL_ITEM={'type':'object','properties':{'text':{'type':'string'},'vulnclass':{'type':'string'}},'required':['text'],'additionalProperties':False}
_SCHEMA={'type':'object','properties':{'goals':{'type':'array','items':_GOAL_ITEM},'text':{'type':'string'},'vulnclass':{'type':'string'}},'additionalProperties':False}

def parse_goals(args):
    if not isinstance(args,dict) or set(args)-set(_SCHEMA['properties']):raise ValueError('Invalid goal fields')
    batch=args.get('goals')
    if batch is not None and not isinstance(batch,list):raise ValueError('goals must be an array')
    items=batch if batch else [{k:v for k,v in args.items() if k!='goals'}]
    if not 1<=len(items)<=32:raise ValueError('One to 32 goals required')
    result=[]
    for item in items:
        if not isinstance(item,dict) or set(item)-{'text','vulnclass'}:raise ValueError('Invalid goal fields')
        text=item.get('text');vc=item.get('vulnclass','')
        if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>16384:raise ValueError('Goal text required and bounded')
        if not isinstance(vc,str) or len(vc.encode('utf-8'))>256:raise ValueError('Invalid vulnerability class')
        row={'text':text.strip()}
        if vc.strip():row['vulnclass']=vc.strip()
        result.append(row)
    return result

class _Transcript:
    def __init__(self,owner,scope,identity):self.owner,self.scope,self.identity=owner,scope,identity;self.original=None
    def begin(self,session,client,user):
        with self.owner.guard(self.scope):
            self.owner.check(self.scope)
            self.original=self.owner.transcript.begin_scoped(session,client,user,self.identity)
            return replace(self.original,messages=[deepcopy(user)])
    def current(self,session,turn):
        with self.owner.guard(self.scope):
            self.owner.check(self.scope)
            return self.owner.transcript.current(session,self.original)
    def commit(self,session,turn,result):
        with self.owner.guard(self.scope):
            self.owner.check(self.scope)
            if not result.messages or result.messages[0]!=turn.messages[0]:raise ChatUnavailable('Goal request changed')
            return self.owner.transcript.commit(session,self.original,replace(result,messages=deepcopy(self.original.messages[:-1])+deepcopy(result.messages)))

class GoalPlanner:
    def __init__(self,*,store,transcript,current,lifecycle_guard,provider_resolver,authorize,validate_input,limits=Limits(),constraints=None,constraint_catalog=None,task_scope=None,scope_catalog=None,activity_projector=None):
        if not isinstance(store,ExplorationStore) or store.read_only:raise ValueError('Writable scoped graph required')
        if any(not callable(f) for f in (current,lifecycle_guard,provider_resolver,authorize,validate_input)):raise ValueError('Explicit scope, provider and permission authority required')
        if any(not callable(getattr(transcript,n,None)) for n in ('begin_scoped','current','commit','finalized_result')):raise ValueError('Durable scoped transcript required')
        if not isinstance(limits,Limits) or limits.max_turns>8:raise ValueError('Goal planning requires at most eight turns')
        if constraints is None:
            if constraint_catalog is not None:raise ValueError('Constraint catalog requires explicit adapter')
        else:
            if not isinstance(constraints,ConstraintTools) or constraints.graph.executor is not store.executor or constraints.store.executor is not store.executor or constraints.guard is not lifecycle_guard:
                raise ValueError('Constraints require exact executor and shared lifecycle guard')
            if not isinstance(constraint_catalog,CatalogTool) or constraint_catalog.key!='set_constraints' or constraint_catalog.enabled is not True or 'goals' not in constraint_catalog.agents or constraint_catalog.deferred is not False or constraint_catalog.kind!='builtin' or constraint_catalog.exec:
                raise ValueError('Explicit enabled native goals constraint catalog required')
        if task_scope is None:
            if scope_catalog is not None:raise ValueError('Scope catalog requires explicit adapter')
        else:
            if not isinstance(task_scope,TaskScopeTools) or task_scope.store.executor is not store.executor or task_scope.guard is not lifecycle_guard:
                raise ValueError('Task scope requires exact executor and shared lifecycle guard')
            if not isinstance(scope_catalog,CatalogTool) or scope_catalog.key!='add_task_scope' or scope_catalog.enabled is not True or 'goals' not in scope_catalog.agents or scope_catalog.deferred is not False or scope_catalog.kind!='builtin' or scope_catalog.exec:
                raise ValueError('Explicit enabled native goals scope catalog required')
        if activity_projector is not None:
            from .activity_projection import ActivityProjector
            if (type(activity_projector) is not ActivityProjector or activity_projector.executor is not store.executor
                or activity_projector.transcript is not transcript or activity_projector.current is not current
                or activity_projector.guard is not lifecycle_guard):
                raise ValueError('Exact optional goals activity projector required')
        self.activity_projector=activity_projector;self._activity_projector=activity_projector
        self.task_scope=task_scope
        self._task_scope_adapter=task_scope
        self._task_scope=task_scope.scope if task_scope is not None else None
        self._scope_catalog=deepcopy(scope_catalog)
        self.constraints=constraints
        self._constraint_scope=constraints.scope if constraints is not None else None
        self._constraint_catalog=deepcopy(constraint_catalog)
        self._store=store
        self._executor=store.executor
        self.store,self.transcript,self.current,self.guard=store,transcript,current,lifecycle_guard
        self.resolver,self.authorize,self.validate_input,self.limits=provider_resolver,authorize,validate_input,limits
    def check(self,scope):
        if self.activity_projector is not self._activity_projector:raise ChatUnavailable('Goals activity projector changed')
        if self._activity_projector is not None:
            p=self._activity_projector
            if (p.scope!=scope or p.executor is not self._executor or p.transcript is not self.transcript
                or p.current is not self.current or p.guard is not self.guard):raise ChatUnavailable('Goals activity binding changed')
        if self.store is not self._store or self.store.executor is not self._executor or self.store.read_only:raise ChatUnavailable('Goal owning resource changed')
        if not isinstance(scope,GoalScope):raise ValueError('Goal scope required')
        for id in (scope.task_id,scope.exploration_id,scope.run_id):
            if int64(id,'scope ID')<=0:raise ValueError('Positive scope required')
        if scope.exploration_id!=self.store.exploration_id or not isinstance(scope.model,Model) or not isinstance(scope.revision,str) or not scope.revision or len(scope.revision)>256 or not isinstance(scope.system,str) or not scope.system.strip():raise ChatUnavailable('Invalid goal planning identity')
        if self.current(scope) is not True:raise RunStopped('stale_goals')
        if self.store is not self._store or self.store.executor is not self._executor or self.store.read_only:raise ChatUnavailable('Goal executor changed')
        if self.task_scope is not self._task_scope_adapter:raise ChatUnavailable('Goal scope adapter changed')
        if self.task_scope is not None:
            adapter=self.task_scope;bound=adapter.scope
            if bound!=self._task_scope or (bound.task_id,bound.exploration_id,bound.run_id,bound.revision)!=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision):raise ChatUnavailable('Goal task scope mismatch')
            if adapter.store.executor is not self._executor or adapter.store.task_id!=scope.task_id or adapter.guard is not self.guard:raise ChatUnavailable('Goal task scope authority changed')
        if self.constraints is not None:
            adapter=self.constraints;bound=adapter.scope
            if bound!=self._constraint_scope or (bound.task_id,bound.exploration_id,bound.run_id,bound.revision,bound.origin)!=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision,'goals'):
                raise ChatUnavailable('Goal constraint scope mismatch')
            if adapter.graph.executor is not self._executor or adapter.store.executor is not self._executor or adapter.store.exploration_id!=scope.exploration_id or adapter.graph.exploration_id!=scope.exploration_id or adapter.graph.read_only or adapter.guard is not self.guard or not callable(adapter.proposals):
                raise ChatUnavailable('Goal constraint authority changed')
    def tool(self,scope):
        def validate(args):parse_goals(args);return True
        def add(ctx,args):
            items=parse_goals(args);ctx.check()
            with self.guard(scope):
                self.check(scope)
                with self.store.executor.transaction() as tx:
                    rows=tx.fetch_all(Query("SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND status IN ('created','running') AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE",(scope.task_id,scope.exploration_id)))
                    if rows not in ([(scope.task_id,)],[[scope.task_id]]):raise ChatUnavailable('Goal task lifecycle unavailable')
                    graph=ExplorationStore(tx,scope.exploration_id);origin=graph.origin_fact_id()
                    if not origin:raise ChatUnavailable('Existing task origin required')
                    ids=[]
                    for item in items:
                        ctx.check();self.check(scope)
                        id=graph.add_goal(item,'goals')
                        if not graph.link(origin,'spawns',id):raise ChatUnavailable('Goal lineage persistence failed')
                        ids.append(id)
                    ctx.check();self.check(scope)
                    return json.dumps({'ids':ids}) if args.get('goals') else 'goal added: '+str(ids[0])
        return Tool('set_goals','Add final verifiable outcomes, not attack steps. Does not mark goals met.',deepcopy(_SCHEMA),add,validate)
    def run(self,scope,client_id,goal,description='',*,token=None):
        if not isinstance(goal,str) or not goal.strip() or not isinstance(description,str):raise ValueError('Explicit task goal and description required')
        with self.guard(scope):
            self.check(scope)
            if self.validate_input(scope,goal,description) is not True:raise ChatUnavailable('Task constraints and scope prerequisites unconfirmed')
            self.check(scope)
        message='Task goal:\n'+goal
        if description.strip():message+='\n\nTask description (background only; do not invent unstated details):\n'+description.strip()
        if len(message.encode('utf-8'))>self.limits.max_request_bytes:raise ValueError('Goal request exceeds bound')
        identity={'task_id':scope.task_id,'exploration_id':scope.exploration_id,'run_id':scope.run_id,'revision':scope.revision,'role':'goals','config_digest':hashlib.sha256(json.dumps({'model':asdict(scope.model),'system':scope.system,'limits':asdict(self.limits),'schema':_SCHEMA,'constraint_scope':asdict(self._constraint_scope) if self._constraint_scope is not None else None,'constraint_catalog':asdict(self._constraint_catalog) if self._constraint_catalog is not None else None,'task_scope':asdict(self._task_scope) if self._task_scope is not None else None,'scope_catalog':asdict(self._scope_catalog) if self._scope_catalog is not None else None},sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()}
        transcript=_Transcript(self,scope,identity)
        def resolver(model):
            with self.guard(scope):
                self.check(scope)
                binding=self.resolver(scope,model)
                self.check(scope)
                return binding
        tools=[self.tool(scope)]
        native=[];catalog=[]
        if self.constraints is not None:native.extend(self.constraints.tools());catalog.append(self._constraint_catalog)
        if self.task_scope is not None:native.extend(self.task_scope.tools());catalog.append(self._scope_catalog)
        if native:
            def registry_current(revision):
                self.check(scope)
                return revision==scope.revision
            registry=ToolRegistry('goals',handlers=native,current=registry_current,lifecycle_guard=lambda rev:self.guard(scope),authorize=self.authorize)
            registry.reload(catalog,scope.revision)
            tools.extend(registry.tools())
        agent=ChatAgent(transcript,resolver,gate=self.authorize,tools=tools,limits=self.limits)
        outcome=agent.run(scope.session_id,client_id,message,model=scope.model,system=scope.system,metadata=identity,token=token)
        if not outcome.persisted or outcome.stale:raise ChatUnavailable('Goal planning persistence unconfirmed')
        if outcome.reused:
            with self.guard(scope):
                self.check(scope);result=self.transcript.finalized_result(scope.session_id,transcript.original)
                if result is None:raise ChatUnavailable('Goal receipt unavailable')
                outcome=replace(outcome,result=replace(result,messages=deepcopy(result.messages[len(transcript.original.messages)-1:])))
        if self._activity_projector is not None:
            # The bootstrap invocation is still owned here. This records only
            # the finalized turn, NOT journal preparation or task completion.
            # Failure propagates to bootstrap quarantine, never a model retry.
            self._activity_projector.project('goals',scope,outcome,identity)
        return outcome
