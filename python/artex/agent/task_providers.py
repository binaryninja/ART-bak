"""Opt-in persisted-role provider selection, not execution authority.

Source server/task_llm.go: goals/planner/worker/mainagent binding, then explicit
active task chain, then global stored profile. Exhausted chains never fall back.
Provisional selection uses explicitly authorized create options without inventing
IDs. Public selection validates configuration without constructing transports.
For enrolled factories only, persisted execution binding constructs from a fresh
selection inside the original configuration guard; provider requests begin after
that guard exits. Every persisted selection on an enrolled factory requires the
explicit factory.build read right in an already-admitted matching owner scope,
including public model selection. Task/execution authorization remains separate. Environment
configuration, quota failover, cache reload and cross-process ownership are absent.

A host-local random HMAC key keeps private configuration out of Model identities;
identities are stable only within this resolver, not restart ownership receipts.
The host must fence configuration writes with the supplied guards and drain calls
before revoking transports. Invalid or unsupported role-bound profiles may fall
back to the explicit task chain, matching source configuration precedence; denied
read grants and storage errors never fall back. Boundary checks are cooperative, not atomic with a
network send and not a sandbox against arbitrary in-process mutation.
"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib,hmac,json,os,sys
from ..db.tasks import TaskStore,TaskCreateOptions
from ..db.agents import AgentStore
from ..db.profiles import ProfileStore,ProfileInvalid
from ..db.core import Query,int64
from ..db.configuration_ownership import is_bound,bound_owner,assert_bound,require_read
from ..providers.factory import ProfileFactory
from ..sidequestion import Model
from .chat import ChatBinding

class TaskProviderUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class SelectedTaskProvider:
    model:Model

_ROLES=('goals','planner','worker','mainagent')

class TaskProviderResolver:
    def __init__(self,tasks,agents,profiles,factory,*,current,lifecycle_guard,
                 authorize_task,authorize_profile,allow_global=False):
        if type(tasks) is not TaskStore or type(agents) is not AgentStore or type(profiles) is not ProfileStore or type(factory) is not ProfileFactory:
            raise ValueError('Actual task, agent, profile stores and profile factory required')
        if tasks.executor is not agents.executor or tasks.executor is not profiles.executor:
            raise ValueError('One exact database executor required')
        if any(not callable(f) for f in (current,lifecycle_guard,authorize_task,authorize_profile)) or type(allow_global) is not bool:
            raise ValueError('Explicit configuration policies required')
        self.tasks,self.agents,self.profiles,self.factory=tasks,agents,profiles,factory
        self.current,self.guard=current,lifecycle_guard
        self.authorize_task,self.authorize_profile=authorize_task,authorize_profile
        self.allow_global=allow_global
        self._resources=(tasks,agents,profiles,factory,tasks.executor,lifecycle_guard,allow_global)
        self._salt=os.urandom(32)

    def _pins(self):return (self.current,self.authorize_task,self.authorize_profile)
    def _resources_check(self,pins):
        t,a,p,f,e,g,global_allowed=self._resources
        if (self.tasks is not t or self.agents is not a or self.profiles is not p or self.factory is not f
            or any(s.executor is not e for s in (t,a,p)) or self.guard is not g or self.allow_global is not global_allowed
            or any(x is not y for x,y in zip(self._pins(),pins))):raise TaskProviderUnavailable('Provider configuration authority changed')
    def _check(self,snapshot,pins):
        self._resources_check(pins)
        if pins[0](deepcopy(snapshot)) is not True:raise TaskProviderUnavailable('Provider configuration is stale')
        self._resources_check(pins)
        if any(x is not y for x,y in zip(self._pins(),pins)):
            raise TaskProviderUnavailable('Provider configuration authority changed')
    def _grant(self,fn,snapshot,pins,*args):
        self._check(snapshot,pins)
        if fn(deepcopy(snapshot),*args) is not True:raise TaskProviderUnavailable('Provider selection not authorized')
        self._check(snapshot,pins)
    def _snapshot(self,value):
        if not isinstance(value,Mapping):raise ValueError('Explicit task snapshot required')
        value=deepcopy(dict(value))
        if value.get('provisional') is True:
            if value.get('id') is not None or value.get('task_id') is not None or value.get('exploration_id') is not None:
                raise ValueError('Provisional selection cannot claim persisted identity')
            options=value.get('options')
            if isinstance(options,TaskCreateOptions):options=asdict(options)
            if not isinstance(options,dict):raise ValueError('Explicit create options required')
            value['options']=options
        else:
            for name in ('task_id','exploration_id'):
                raw=value.get(name,value.get('id') if name=='task_id' else None)
                if isinstance(raw,str) and raw.isdecimal():raw=int(raw)
                if int64(raw,name)<=0:raise ValueError('Positive persisted task identity required')
                value[name]=raw
        return value
    @staticmethod
    def _ids(ids):
        if type(ids) is not list or len(ids)>100:raise TaskProviderUnavailable('Bounded task profile chain required')
        if any(type(i) is not int or not 0<i<=2**63-1 for i in ids) or len(set(ids))!=len(ids):
            raise TaskProviderUnavailable('Invalid task profile chain')
        return ids
    def _selection(self,snapshot,role,*,build_for=None):
        if role not in _ROLES:raise ValueError('Unsupported task role')
        snapshot=self._snapshot(snapshot);pins=self._pins();selected=None
        if build_for is not None and (not isinstance(build_for,Model) or snapshot.get('provisional') is True):
            raise ValueError('Expected model and persisted task selection required')
        construction_complete=False;construction_error=None;provider=None
        construction_owner=None
        owned_selection=(snapshot.get('provisional') is not True and is_bound(self._resources[3]))
        if owned_selection or build_for is not None:
            self._resources_check(pins)
            construction_owner=bound_owner(self._resources[3])
            assert_bound(self._resources[4],construction_owner)
        with self._resources[5](deepcopy(snapshot)):
            try:
                if owned_selection or build_for is not None:
                    self._resources_check(pins)
                    assert_bound(self._resources[3],construction_owner)
                    assert_bound(self._resources[4],construction_owner)
                    require_read(self._resources[3],'factory.build')
                self._grant(pins[1],snapshot,pins,role)
                if snapshot.get('provisional') is True:
                    chain=self._ids(snapshot['options'].get('llm_profile_ids',[]));active=chain[0] if chain else None
                else:
                    task=self.tasks.get(snapshot['task_id']);self._check(snapshot,pins)
                    if task is None or task.exploration_id!=snapshot['exploration_id']:
                        raise TaskProviderUnavailable('Persisted task identity unavailable')
                    chain=self._ids(task.llm_profile_ids);active=task.active_llm_profile_id
                agent=self.agents.get(role);self._check(snapshot,pins)
                if agent is None or not agent.enabled or agent.key!=role:
                    raise TaskProviderUnavailable('Enabled persisted task role required')
                def profile(identifier):
                    if type(identifier) is not int or identifier<=0:raise TaskProviderUnavailable('Invalid profile identity')
                    self._grant(pins[2],snapshot,pins,role,identifier)
                    p=self.profiles.get(identifier);self._check(snapshot,pins)
                    self._grant(pins[2],snapshot,pins,role,identifier)
                    if p is None:return None
                    try:self.factory.validate(p)
                    except ProfileInvalid:return None
                    self._check(snapshot,pins)
                    return p
                if agent.llm_profile_id is not None:selected=profile(agent.llm_profile_id)
                if selected is None and chain:
                    if active is None or active not in chain:raise TaskProviderUnavailable('Task profile chain exhausted or invalid')
                    selected=profile(active)
                    if selected is None:raise TaskProviderUnavailable('Task profile unavailable')
                if selected is None and self.allow_global:
                    rows=self._resources[4].fetch_all(Query('SELECT id FROM llm_profiles WHERE is_default LIMIT 2',()))
                    self._check(snapshot,pins)
                    if len(rows)>1:raise TaskProviderUnavailable('Ambiguous active profile')
                    if rows:selected=profile(rows[0][0])
                if selected is None:raise TaskProviderUnavailable('No authorized stored provider; environment fallback unsupported')
                # Full private profile and role configuration are hashed, never returned.
                identity={'profile':asdict(selected),'agent':asdict(agent),'chain':chain,'active':active,'role':role}
                digest=hmac.new(self._salt,json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode(),hashlib.sha256).hexdigest()
                model=Model(profile_id=selected.id,name=selected.name,format=selected.format,model=selected.model,
                    identity=digest,streaming=selected.streaming,window_tokens=max(32,min(1000,selected.context_window_k or 200))*1000)
                if build_for is not None:
                    if model!=build_for:raise TaskProviderUnavailable('Task role provider changed')
                    provider=self.factory.build(selected)
                self._grant(pins[1],snapshot,pins,role)
                self._grant(pins[2],snapshot,pins,role,selected.id)
                construction_complete=True
            except BaseException as exc:
                construction_error=exc
                raise
        if owned_selection or build_for is not None:
            if construction_error is not None:raise construction_error
            if not construction_complete:raise TaskProviderUnavailable('Provider construction guard suppressed work')
        self._grant(pins[1],snapshot,pins,role)
        self._grant(pins[2],snapshot,pins,role,selected.id)
        if build_for is not None:return model,deepcopy(selected),provider
        return model,deepcopy(selected)
    def select(self,task_snapshot,role):
        model,_=self._selection(task_snapshot,role)
        return SelectedTaskProvider(model)
    def bind(self,scope,role,expected_model,*,current,lifecycle_guard):
        if not isinstance(expected_model,Model) or not callable(current) or not callable(lifecycle_guard):
            raise ValueError('Explicit model and execution lifetime authority required')
        for key in ('task_id','exploration_id','run_id'):
            if int64(getattr(scope,key,None),key)<=0:raise ValueError('Exact persisted execution scope required')
        if not isinstance(scope.revision,str) or not scope.revision:raise ValueError('Execution revision required')
        if getattr(scope,'model',None)!=expected_model:raise ValueError('Exact scope model required')
        wrapper=_BoundProvider(self,scope,role,expected_model,current,lifecycle_guard)
        wrapper.check()
        return ChatBinding(expected_model,wrapper)

class _BoundProvider:
    def __init__(self,owner,scope,role,model,current,guard):
        self.owner,self.scope,self.role,self.model=owner,scope,role,model
        self.current,self.guard=current,guard
        self.identity=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision)
        self._pins=(owner,scope,role,model,current,guard)
        self._configuration_pins=owner._pins()
    def fixed(self):
        self.owner._resources_check(self._configuration_pins)
        if any(x is not y for x,y in zip((self.owner,self.scope,self.role,self.model,self.current,self.guard),self._pins)):
            raise TaskProviderUnavailable('Execution provider binding changed')
        if self.scope.model!=self.model or (self.scope.task_id,self.scope.exploration_id,self.scope.run_id,self.scope.revision)!=self.identity:
            raise TaskProviderUnavailable('Execution provider identity changed')
    def check(self,context=None):
        self.fixed()
        if context is not None:(context.get('runtime_context',context) if isinstance(context,Mapping) else context).check()
        with self.guard(self.scope):
            if (self.scope.task_id,self.scope.exploration_id,self.scope.run_id,self.scope.revision)!=self.identity or self.current(self.scope) is not True:
                raise TaskProviderUnavailable('Installed task provider owner unavailable')
            self.fixed()
        model,profile=self.owner._selection({'task_id':self.identity[0],'exploration_id':self.identity[1]},self.role)
        if model!=self.model:raise TaskProviderUnavailable('Task role provider changed')
        with self.guard(self.scope):
            self.fixed()
            if self.current(self.scope) is not True:raise TaskProviderUnavailable('Installed task provider owner changed')
            self.fixed()
            if context is not None:(context.get('runtime_context',context) if isinstance(context,Mapping) else context).check()
        return profile
    def _provider(self,context):
        if not is_bound(self.owner.factory):
            profile=self.check(context)
            return self.owner.factory.build(profile)
        # Refuse mismatched enrollment before check() can read stored profiles.
        self.fixed()
        construction_owner=bound_owner(self.owner._resources[3])
        assert_bound(self.owner._resources[4],construction_owner)
        self.check(context)
        # Enrolled factories require selection and construction inside the
        # original configuration guard. Execution/task grants remain separate.
        _,_,provider=self.owner._selection(
            {'task_id':self.identity[0],'exploration_id':self.identity[1]},
            self.role,build_for=self.model)
        return provider
    def complete(self,context,request):
        provider=self._provider(context)
        self.check(context)
        result=provider.complete(context,request)
        self.check(context)
        return result
    def stream(self,context,request):
        provider=self._provider(context)
        self.check(context);iterator=iter(provider.stream(context,request))
        try:
            while True:
                self.check(context)
                try:event=next(iterator)
                except StopIteration:break
                self.check(context)
                yield event
        finally:
            # Always attempt cleanup and final fences, but never replace the
            # originating cancellation/provider failure with a cleanup error.
            original=sys.exc_info()[0] is not None
            failure=None
            try:
                close=getattr(iterator,'close',None)
                if callable(close):close()
            except BaseException as exc:failure=exc
            try:self.check(context)
            except BaseException as exc:
                if failure is None:failure=exc
            if failure is not None and not original:raise failure
