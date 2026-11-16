"""One explicit bounded planner cycle with real durable graph intent dispatch.

Source agent/planner.go Plan: event/heartbeat wake, fresh working context, accumulating
exp<ID>-planner raw transcript, planner-bound tools, persisted goals/frontier. This
subset implements add_intent on the actual ExplorationStore, local fact/finding
lineage, explicit asset authorization, transaction parent guard and bounded batch.
No inherited-parent mutation, automatic prove_goal registration, autonomous worker launching, compaction,
shared todo, timeout-final settlement, task revival or task-completion claim.

prepare captures a bounded immutable Wake. Retrying MUST reuse that exact wake:
changed situation under the same cycle ID conflicts rather than re-executing.
ChatTranscriptAdapter scoped admission/commit is durable; SQL tool effects and
JSONL terminal commit are NOT atomic. Unresolved transcript admission blocks replay.
Host current(scope)/lifecycle_guard(scope) must share task run/revision, pause,
archive/deletion and config authority; no implicit AgentHost adapter exists.
Optional additional_tools/additional_catalog are explicit native host declarations,
validated by the same permission-gated scoped registry. add_intent is reserved,
custom executable specs remain unsupported, and defaults expose no extra tools.
Supplying a callback does not seed a catalog, unlock deferred tools or grant it.
Construction starts nothing. Providers/tools in tests are inert, never defaults.

Optional persist_current(scope) is explicit SAME-generation finalization authority,
used only for transcript commit under the shared guard. It must independently
reject deleted/replaced task/run/transcript/profile/policy state, while optionally
allowing a cancelled current generation to record its terminal receipt. It never
authorizes admission, model calls, tools or graph observation. Without it, current
remains required for commit. Authority changes require a host revision bump.
Cancelled durable outcomes have observed=False and None graph fields, never a
false empty frontier or successful goal observation.
"""
from copy import deepcopy
from dataclasses import dataclass,replace,asdict
from threading import RLock
import json,hashlib
from .chat import ChatAgent,ChatBusy,ChatUnavailable
from .runtime import Tool,Grant,Limits,RunStopped,RunResult
from ..db.core import Query,int64
from ..db.exploration import ExplorationStore
from ..db.tool_catalog import CatalogTool
from ..tools.registry import ToolRegistry
from ..chat_dispatch import Cancellation
from ..sidequestion import Model

@dataclass(frozen=True)
class PlannerScope:
    task_id:int
    exploration_id:int
    run_id:int
    revision:str
    model:Model
    system:str
    @property
    def session_id(self):return f'exp{self.exploration_id}-planner'
    def identity(self):
        model=json.dumps(asdict(self.model),sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
        return dict(task_id=self.task_id,exploration_id=self.exploration_id,run_id=self.run_id,revision=self.revision,role='planner',model_digest=hashlib.sha256(model).hexdigest(),system_digest=hashlib.sha256(self.system.encode('utf-8')).hexdigest())

@dataclass(frozen=True)
class PlannerWake:
    scope:PlannerScope
    cycle_id:str
    message:str

@dataclass(frozen=True)
class PlannerResult:
    outcome:object
    active_intents:bool|None
    goals_met:bool|None
    stalled:bool|None
    observed:bool=True
    cleanup:object=None

_INTENT_SCHEMA={'type':'object','properties':{'summary':{'type':'string'},'priority':{'type':'integer'},'asset_ids':{'type':'array','items':{'type':'integer'}},'parent_ids':{'type':'array','items':{'type':'integer'}},'intents':{'type':'array','items':{'type':'object'}}},'additionalProperties':False}

class _FreshTranscript:
    def __init__(self,owner,scope):self.owner,self.scope=owner,scope;self.original=None;self.executor=owner.store.executor;self.cleanup=owner.cleanup;self.cap=None
    def begin(self,session,client,user):
        with self.owner.guard(self.scope):
            self.owner.check(self.scope)
            original=self.owner.transcript.begin_scoped(session,client,user,dict(self.scope.identity(),limits_digest=hashlib.sha256(json.dumps(asdict(self.owner.limits),sort_keys=True,allow_nan=False).encode()).hexdigest(),tool_catalog_digest=self.owner._tool_catalog_digest))
            self.original=original
            if self.cleanup is not None:
                identity=dict(self.scope.identity(),limits_digest=hashlib.sha256(json.dumps(asdict(self.owner.limits),sort_keys=True,allow_nan=False).encode()).hexdigest(),tool_catalog_digest=self.owner._tool_catalog_digest)
                self.cap=self.cleanup.reserve('planner',self.scope,session,original,identity)
            return replace(original,messages=[deepcopy(user)])
    def current(self,session,turn):
        with self.owner.guard(self.scope):
            self.owner.check(self.scope)
            return self.owner.transcript.current(session,self.original)
    def commit(self,session,turn,result):
        if self.cleanup is not self.owner.cleanup:raise ChatUnavailable('Cleanup resource changed')
        if self.cleanup is not None:
            if not result.messages or result.messages[0]!=turn.messages[0]:raise ChatUnavailable('Planner rewrote wake')
            joined=replace(result,messages=deepcopy(self.original.messages[:-1])+deepcopy(result.messages))
            return self.cleanup.commit(self.cap,self.original,joined)
        with self.owner.guard(self.scope):
            self.owner.check_persistence(self.scope,self.executor)
            if not result.messages or result.messages[0]!=turn.messages[0]:raise ChatUnavailable('Planner rewrote wake')
            joined=replace(result,messages=deepcopy(self.original.messages[:-1])+deepcopy(result.messages))
            return self.owner.transcript.commit(session,self.original,joined)

class PlannerCycle:
    def __init__(self,*,store,transcript,current,lifecycle_guard,provider_resolver,authorize,asset_authorize,limits=Limits(),max_nodes=2000,additional_tools=(),additional_catalog=(),persist_current=None,cleanup=None):
        if not isinstance(store,ExplorationStore) or store.read_only:raise ValueError('Writable scoped ExplorationStore required')
        if any(not callable(v) for v in (current,lifecycle_guard,provider_resolver,authorize,asset_authorize)):raise ValueError('Explicit planner authority required')
        if any(not callable(getattr(transcript,n,None)) for n in ('begin_scoped','current','commit','finalized_result')):raise ValueError('Durable scoped transcript required')
        if persist_current is not None and not callable(persist_current):raise ValueError('Explicit persistence authority must be callable')
        if type(max_nodes) is not int or not 1<=max_nodes<=10000:raise ValueError('Invalid graph bound')
        self.store,self.transcript,self.current,self.guard=store,transcript,current,lifecycle_guard
        self._store,self._executor=store,store.executor
        if cleanup is not None:
            from .inflight_cleanup import InflightCleanupOwner
            if type(cleanup) is not InflightCleanupOwner:raise ValueError('Exact cleanup owner required')
        self.cleanup=cleanup
        self.persist_current=persist_current
        self.provider,self.authorize,self.asset_authorize=provider_resolver,authorize,asset_authorize
        supplied=tuple(additional_tools);catalog=tuple(additional_catalog)
        if len(supplied)>1000 or len(catalog)>1000:raise ValueError('Additional planner tool bound exceeded')
        names=[tool.name for tool in supplied if isinstance(tool,Tool)]
        if len(names)!=len(supplied) or len(set(names))!=len(names) or 'add_intent' in names:
            raise ValueError('Unique native tools required; add_intent is reserved')
        if any(not isinstance(row,CatalogTool) for row in catalog):raise ValueError('Explicit native catalog declarations required')
        if len({row.key for row in catalog})!=len(catalog) or {row.key for row in catalog}!=set(names):
            raise ValueError('Additional tool/catalog identities must match exactly')
        # Snapshot annotations/defaults and bindings. Callback authority remains
        # host-owned; catalog metadata never grants execution permission.
        self._additional_tools=tuple(Tool(t.name,t.description,deepcopy(t.input_schema),t.callback,t.validate) for t in supplied)
        self._additional_catalog=deepcopy(catalog)
        # Stable metadata identity survives restart. Callback/policy code identity
        # cannot be inferred: the host MUST bump scope.revision when it changes.
        declaration={'handlers':[{'name':t.name,'description':t.description,'schema':t.input_schema} for t in sorted(self._additional_tools,key=lambda t:t.name)],
                     'catalog':[asdict(row) for row in sorted(self._additional_catalog,key=lambda r:r.key)]}
        self._tool_catalog_digest=hashlib.sha256(json.dumps(declaration,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf-8')).hexdigest()
        self.limits,self.max_nodes=limits,max_nodes;self._lock=RLock();self._active={}
    def bind_cleanup(self,owner):
        from .inflight_cleanup import InflightCleanupOwner
        if type(owner) is not InflightCleanupOwner or owner.transcript is not self.transcript or owner.handle.executor is not self.store.executor:raise ValueError('Original cleanup resources required')
        with self._lock:
            if self._active or self.cleanup is not None:raise ValueError('Cleanup binding requires inert planner')
            self.cleanup=owner

    def _validate_scope(self,scope):
        if self.store is not self._store or self.store.executor is not self._executor:raise ChatUnavailable('Planner owning resource changed')
        if not isinstance(scope,PlannerScope):raise ValueError('Planner scope required')
        for id in (scope.task_id,scope.exploration_id,scope.run_id):
            if int64(id,'scope ID')<=0:raise ValueError('Positive scope identity required')
        if not isinstance(scope.model,Model) or not isinstance(scope.revision,str) or len(scope.revision)>256:raise ValueError('Explicit model and revision required')
        if scope.exploration_id!=self.store.exploration_id or not scope.revision or not isinstance(scope.system,str):raise ChatUnavailable('Planner scope mismatch')
        if self.store.read_only:raise ChatUnavailable('Planner store binding changed')
    def check(self,scope):
        self._validate_scope(scope)
        if self.current(scope) is not True:raise RunStopped('stale_planner')
        self._validate_scope(scope)
    def check_persistence(self,scope,executor):
        # ONLY transcript finalization may use this separate authority. A pause
        # can revoke execution without revoking persistence of its own receipt.
        self._validate_scope(scope)
        if self.store.executor is not executor:raise ChatUnavailable('Planner persistence store changed')
        callback=self.persist_current if self.persist_current is not None else self.current
        if callback(scope) is not True:raise RunStopped('stale_planner_persistence')
        self._validate_scope(scope)
        if self.store.executor is not executor:raise ChatUnavailable('Planner persistence store changed')
    def _nodes(self):
        nodes=self.store.nodes(self.max_nodes+1)
        if len(nodes)>self.max_nodes:raise ChatUnavailable('Planner graph exceeds bounded cycle')
        return nodes
    def prepare(self,scope,cycle_id,triggers=()):
        if not isinstance(cycle_id,str) or not cycle_id:raise ValueError('Stable cycle ID required')
        triggers=deepcopy(list(triggers))
        with self.guard(scope):
            self.check(scope);nodes=self._nodes()
            if self.store.task_id()!=scope.task_id:raise ChatUnavailable('Planner task mismatch')
            situation=[dict(id=n.id,kind=n.kind,state=n.state,payload=n.payload,priority=n.priority) for n in nodes]
        lead='Specific changes triggered this cycle.' if triggers else 'Heartbeat wake; do not assume the graph changed.'
        message=lead+'\n'+json.dumps({'triggers':triggers,'nodes':situation},ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(message.encode())>self.limits.max_request_bytes:raise ChatUnavailable('Planner wake exceeds bound')
        return PlannerWake(scope,cycle_id,message)
    def _items(self,args):
        if not isinstance(args,dict) or set(args)-set(_INTENT_SCHEMA['properties']):raise ValueError('Invalid intent fields')
        items=args.get('intents') or [args]
        if not isinstance(items,list) or not 1<=len(items)<=4:raise ValueError('One to four intents required')
        normalized=[]
        for item in items:
            if not isinstance(item,dict) or set(item)-{'summary','priority','asset_ids','parent_ids'}:raise ValueError('Invalid intent')
            summary=item.get('summary')
            if not isinstance(summary,str) or not summary.strip() or len(summary.encode())>16384:raise ValueError('Intent summary required')
            priority=item.get('priority',5) or 5
            if type(priority) is not int or not 0<=priority<=10:raise ValueError('Invalid intent priority')
            ids=[]
            for field in ('asset_ids','parent_ids'):
                values=item.get(field,[])
                if not isinstance(values,list) or len(values)>100:raise ValueError('Invalid intent IDs')
                for id in values:
                    if int64(id,'intent reference')<=0:raise ValueError('Positive reference required')
                ids.append(list(dict.fromkeys(values)))
            normalized.append((summary,priority,*ids))
        return normalized
    def intent_tool(self,scope):
        def validate(args):self._items(args);return True
        def add(context,args):
            items=self._items(args);context.check()
            with self.guard(scope):
                self.check(scope)
                with self.store.executor.transaction() as tx:
                    parent=tx.fetch_one(Query("SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND status='running' AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE",(scope.task_id,scope.exploration_id)))
                    if parent is None or tuple(parent)!=(scope.task_id,):raise ChatUnavailable('Planner task is not live running')
                    graph=ExplorationStore(tx,scope.exploration_id);created=[]
                    for summary,priority,assets,parents in items:
                        context.check();self.check(scope)
                        grant=self.asset_authorize(scope,tuple(assets),summary)
                        if not isinstance(grant,Grant) or any(v is not True for v in (grant.permission,grant.in_scope,grant.approved)):raise ChatUnavailable('Intent scope authorization denied')
                        self.check(scope);context.check()
                        if not parents:
                            origin=graph.origin_fact_id()
                            if not origin:raise ChatUnavailable('Task origin fact unavailable')
                            parents=[origin]
                        for id in parents:
                            node=graph.get_node(id)
                            if node is None or node.kind not in ('fact','finding'):raise ValueError('Local fact/finding parent required')
                        payload={'summary':summary}
                        if assets:payload['asset_ids']=assets
                        id=graph.add_intent(payload,priority,assets,'planner')
                        for parent in parents:
                            if not graph.link(parent,'derived_from',id):raise ChatUnavailable('Intent lineage persistence failed')
                        created.append(id)
                    return json.dumps({'ids':created}) if args.get('intents') else 'intent created: '+str(created[0])
        return Tool('add_intent','Persist authorized exploration directions with source lineage.',deepcopy(_INTENT_SCHEMA),add,validate)
    def cancel(self,task_id):
        with self._lock:token=self._active.get(task_id)
        if token is None:return False
        token.cancel();return True
    def run(self,wake):
        if not isinstance(wake,PlannerWake):raise ValueError('Prepared immutable wake required')
        scope=wake.scope;token=Cancellation()
        with self.guard(scope):
            self.check(scope)
            with self._lock:
                if scope.task_id in self._active:raise ChatBusy('Planner cycle active')
                self._active[scope.task_id]=token
        fresh=None
        try:
            tool=self.intent_tool(scope)
            registry=ToolRegistry('planner',handlers=[tool,*self._additional_tools],current=lambda rev:rev==scope.revision and self.current(scope) is True,lifecycle_guard=lambda rev:self.guard(scope),authorize=self.authorize)
            registry.reload([CatalogTool(tool.name,True,tool.description,tool.input_schema,['planner'],True,'builtin',{},False),*self._additional_catalog],scope.revision)
            fresh=_FreshTranscript(self,scope)
            def resolver(model):
                token.check()
                with self.guard(scope):
                    self.check(scope)
                    binding=self.provider(scope,model)
                    self.check(scope)
                    return binding
            agent=ChatAgent(fresh,resolver,gate=self.authorize,tools=registry.tools(),limits=self.limits)
            outcome=agent.run(scope.session_id,wake.cycle_id,wake.message,model=scope.model,system=scope.system,metadata=scope.identity(),token=token)
            if not outcome.persisted or outcome.stale:raise ChatUnavailable('Planner transcript persistence unconfirmed')
            if fresh.cap is not None:outcome=fresh.cleanup.canonical_outcome(fresh.cap,outcome)
            if outcome.reused:
                with self.guard(scope):
                    self.check(scope)
                    durable=self.transcript.finalized_result(scope.session_id,fresh.original)
                    if durable is None:raise ChatUnavailable('Planner durable result unavailable')
                    outcome=replace(outcome,result=replace(durable,messages=deepcopy(durable.messages[len(fresh.original.messages)-1:])))
            if token.cancelled or (outcome.result is not None and outcome.result.reason=='cancelled'):
                # A committed cancelled turn is not graph completion evidence.
                # Do not re-enter execution authority merely to observe nodes.
                return PlannerResult(outcome,None,None,None,observed=False,cleanup=fresh.cap)
            with self.guard(scope):
                if token.cancelled:return PlannerResult(outcome,None,None,None,observed=False,cleanup=fresh.cap)
                try:self.check(scope)
                except RunStopped:
                    # Cancellation may win between the first check and the
                    # execution-current callback. Commit already confirmed its
                    # own authority; returning no observation grants nothing.
                    if token.cancelled:return PlannerResult(outcome,None,None,None,observed=False,cleanup=fresh.cap)
                    raise
                if token.cancelled:return PlannerResult(outcome,None,None,None,observed=False,cleanup=fresh.cap)
                nodes=self._nodes()
                if token.cancelled:return PlannerResult(outcome,None,None,None,observed=False,cleanup=fresh.cap)
                goals=[n for n in nodes if n.kind=='goal'];met=bool(goals) and all(n.state=='met' for n in goals)
                active=any(n.kind=='intent' and n.state in ('open','running') for n in nodes)
            return PlannerResult(outcome,active,met,not active and not met,cleanup=fresh.cap)
        except BaseException:
            if fresh is not None and fresh.cap is not None:
                try:fresh.cleanup.quarantine(fresh.cap)
                except BaseException:pass
            raise
        finally:
            with self._lock:
                if self._active.get(scope.task_id) is token:self._active.pop(scope.task_id)
