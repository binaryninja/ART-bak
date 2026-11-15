"""Explicit persisted-role bootstrap/engine composition, without a second manager.

Construction is inert. Host policies, prompts, transcript storage and graph-tool
catalogs are mandatory inputs, not discovered permissions. Only local graph tools
are supported here. No process/MCP/network tools, inherited exploration, restart
adoption, timed execution, quota failover, or automatic pump are supplied.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib,json
from .task_providers import TaskProviderResolver
from .bootstrap import BootstrapCoordinator,BootstrapJournal
from .bootstrap_setup import BootstrapConfiguration
from .goals import GoalPlanner
from .planner import PlannerCycle,PlannerScope
from .worker import IntentWorker
from .engine import TaskEngineHandle
from .runtime import Limits
from ..db.exploration import ExplorationStore
from ..db.workers import WorkerStore
from ..tools.graph import GraphTools,GraphScope
from ..tools.registry import ToolRegistry
from ..manager import RuntimeUnavailable
from ..chat_transcripts import ChatTranscriptAdapter
from ..transcripts import TranscriptStore

class TaskRuntimeAssembly:
    def __init__(self,executor,*,providers,transcript,prompts,policy_revision,
                 current,lifecycle_guard,authorize,asset_authorize,validate_input,
                 graph_catalog,limits=Limits()):
        if type(providers) is not TaskProviderResolver or providers.tasks.executor is not executor:
            raise ValueError('Exact persisted provider resolver/executor required')
        if any(not callable(x) for x in (current,lifecycle_guard,authorize,asset_authorize,validate_input)):
            raise ValueError('Explicit host authority required')
        if not isinstance(policy_revision,str) or not 1<=len(policy_revision)<=128 or not isinstance(limits,Limits):
            raise ValueError('Bounded policy revision and runtime limits required')
        if set(prompts)!= {'goals','planner','worker'} or any(not isinstance(v,str) or not v.strip() for v in prompts.values()):
            raise ValueError('Explicit goals, planner and worker prompts required')
        if set(graph_catalog)!= {'planner','worker'}:raise ValueError('Explicit graph catalog per execution role required')
        if type(transcript) is not ChatTranscriptAdapter or type(transcript.store) is not TranscriptStore:
            raise ValueError('Durable scoped transcript adapter required')
        self.executor=executor;self.providers=providers;self.transcript=transcript
        self._prompts=deepcopy(prompts);self._catalog=deepcopy(graph_catalog)
        self._revision=policy_revision;self._current=current;self._guard=lifecycle_guard
        self._authorize=authorize;self._assets=asset_authorize;self._input=validate_input;self._limits=limits
        self._pins=(executor,providers,transcript,providers.tasks,providers.agents,providers.profiles,providers.factory)
        self._configurations={}
        self._transcript_store=transcript.store
        self._transcript_identity=(transcript.store.root,transcript.store.max_bytes,transcript.agent_id,transcript.recovery_guard)
        self._policy_pins=(current,lifecycle_guard,authorize,asset_authorize,validate_input,limits,policy_revision)
        self._configuration_bytes=self._configuration_identity()
        self.coordinator=BootstrapCoordinator(BootstrapJournal(executor),planner_factory=self._goals,
            lifecycle_guard=lifecycle_guard,current=self._bootstrap_current)
        self._coordinator=self.coordinator
        self._coordinator_pins=(self.coordinator.journal,self.coordinator.factory,self.coordinator.current)
    def _configuration_identity(self):
        return json.dumps({'prompts':self._prompts,'catalog':{k:[asdict(r) for r in v] for k,v in self._catalog.items()}},sort_keys=True,allow_nan=False)
    def validate_bindings(self):
        e,p,t,tasks,agents,profiles,factory=self._pins
        if any(a is not b for a,b in zip((self._current,self._guard,self._authorize,self._assets,self._input,self._limits,self._revision),self._policy_pins)) or self._configuration_identity()!=self._configuration_bytes or t.store is not self._transcript_store:
            raise RuntimeUnavailable('Task runtime policy or transcript changed')
        if (t.store.root,t.store.max_bytes,t.agent_id,t.recovery_guard)!=self._transcript_identity:
            raise RuntimeUnavailable('Task runtime transcript namespace changed')
        if self.coordinator.journal is not self._coordinator_pins[0] or self.coordinator.factory is not self._coordinator_pins[1] or self.coordinator.current is not self._coordinator_pins[2]:
            raise RuntimeUnavailable('Task runtime coordinator authority changed')
        if self.executor is not e or self.providers is not p or self.transcript is not t or self.coordinator is not self._coordinator:
            raise RuntimeUnavailable('Task runtime resource changed')
        if p.tasks is not tasks or p.agents is not agents or p.profiles is not profiles or p.factory is not factory or any(s.executor is not e for s in (tasks,agents,profiles)):
            raise RuntimeUnavailable('Task runtime provider resource changed')
        if self.coordinator.journal.executor is not e or self.coordinator.guard is not self._guard:
            raise RuntimeUnavailable('Task runtime coordinator changed')
        return True
    def _select(self,task):
        self.validate_bindings()
        models={role:self.providers.select(deepcopy(task),role).model for role in ('goals','planner','worker')}
        payload={'models':{k:asdict(v) for k,v in models.items()},'prompts':self._prompts,
                 'catalog':{k:[asdict(r) for r in v] for k,v in self._catalog.items()},
                 'limits':asdict(self._limits),'policy':self._revision}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
        self.validate_bindings();return models,digest
    def bootstrap_configuration(self,task):
        models,digest=self._select(task)
        # Cache immutable models only, never profile secrets or provisional IDs.
        previous=self._configurations.get(digest)
        if previous is not None and previous!=models:raise RuntimeUnavailable('Configuration identity collision')
        if len(self._configurations)>=10000 and previous is None:raise RuntimeUnavailable('Configuration capacity exhausted')
        self._configurations[digest]=models
        return BootstrapConfiguration(models['goals'],self._prompts['goals'],digest,'bootstrap-'+digest)
    def _bootstrap_current(self,task_id,exploration_id,revision):
        self.validate_bindings()
        if self._current(task_id,exploration_id,self._revision) is not True:return False
        _,digest=self._select({'task_id':task_id,'exploration_id':exploration_id})
        self.validate_bindings();return digest==revision
    def _goals(self,scope,current,guard):
        self.validate_bindings()
        return GoalPlanner(store=ExplorationStore(self.executor,scope.exploration_id),transcript=self.transcript,
            current=current,lifecycle_guard=guard,provider_resolver=lambda s,m:self.providers.bind(s,'goals',m,current=current,lifecycle_guard=guard),
            authorize=self._authorize,validate_input=self._input,limits=self._limits)
    def engine_factory(self,row,token,scope,current,guard):
        # Owner's engine authority is intentionally false until next explicit
        # tick. Configuration reads here are not model execution admission.
        models,digest=self._select(row)
        if digest!=scope.revision or digest not in self._configurations:raise RuntimeUnavailable('Prepared runtime configuration changed')
        graph=ExplorationStore(self.executor,scope.exploration_id)
        def exact(s):
            self.validate_bindings()
            if (s.task_id,s.exploration_id,s.run_id,s.revision)!=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision) or current(scope,token) is not True:return False
            if self._current(scope.task_id,scope.exploration_id,self._revision) is not True:return False
            _,live_digest=self._select({'task_id':scope.task_id,'exploration_id':scope.exploration_id})
            result=live_digest==scope.revision and self._current(scope.task_id,scope.exploration_id,self._revision) is True and current(scope,token) is True
            self.validate_bindings();return result
        def guarded(s):
            if (s.task_id,s.exploration_id,s.run_id,s.revision)!=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision):raise RuntimeUnavailable('Foreign runtime scope')
            return guard(scope)
        def graph_tools(role,s,check):
            gs=GraphScope(s.task_id,s.exploration_id,s.run_id,s.revision,role,getattr(s,'intent_id',0))
            tools=GraphTools(graph,gs,current=lambda _:check(),lifecycle_guard=lambda _:guarded(s),authorize=self._authorize,asset_authorize=lambda scope,ids:self._assets(scope,ids,None)).tools()
            names={r.key for r in self._catalog[role]}
            if not names.issubset({t.name for t in tools}):raise RuntimeUnavailable('Only explicit local graph tools supported')
            return tuple(t for t in tools if t.name in names)
        ps=PlannerScope(scope.task_id,scope.exploration_id,scope.run_id,scope.revision,models['planner'],self._prompts['planner'])
        planner=PlannerCycle(store=graph,transcript=self.transcript,current=exact,lifecycle_guard=guarded,
            provider_resolver=lambda s,m:self.providers.bind(s,'planner',m,current=exact,lifecycle_guard=guarded),
            authorize=self._authorize,asset_authorize=self._assets,limits=self._limits,
            additional_tools=graph_tools('planner',ps,lambda:exact(ps)),additional_catalog=self._catalog['planner'])
        worker=None
        def registry(s,record):
            def owned():
                worker.check(s,'tool',claimed=True);return True
            reg=ToolRegistry('worker',handlers=graph_tools('worker',s,owned),current=lambda revision:revision==scope.revision and exact(s),
                lifecycle_guard=lambda _:guarded(s),authorize=self._authorize)
            reg.reload(self._catalog['worker'],scope.revision);return reg
        worker=IntentWorker(store=WorkerStore(self.executor,scope.task_id,scope.exploration_id,
            lifetime_guard=lambda tid,eid,write:guard(scope)),transcript=self.transcript,
            current=lambda s,phase:exact(s),lifecycle_guard=guarded,
            provider_resolver=lambda s,m:self.providers.bind(s,'worker',m,current=exact,lifecycle_guard=guarded),
            registry_factory=registry,authorize=self._authorize,limits=self._limits)
        return TaskEngineHandle(scope,token,store=graph,planner=planner,planner_scope=ps,worker=worker,
            worker_model=models['worker'],worker_system=self._prompts['worker'],owner='runtime-'+str(scope.run_id),
            current=current,lifecycle_guard=guard,single_process=True)
    @staticmethod
    def initialized_readiness(task):raise RuntimeUnavailable('Initialized task adoption requires explicit recovery and is unsupported')
    @staticmethod
    def initialized_factory(*args):raise RuntimeUnavailable('Initialized task adoption is unsupported')
