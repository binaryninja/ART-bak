"""Explicit persisted-role bootstrap/engine composition, without a second manager.

Construction is inert. Host policies, prompts, transcript storage and graph-tool
catalogs are mandatory inputs, not discovered permissions. Local graph tools are the default. An explicit worker_process_policy adds only
foreground Bash through a host-injected runner and independent drain ledger.
No default process runner, MCP/network tools, inherited exploration, restart
adoption, timed execution, quota failover, or automatic pump are supplied.
Optional TaskTimingPolicy composes inert original-owner timing resources and
planner/worker wrappers only after prepared binding. It requires explicit cleanup
permission, pins clocks/grants, and includes its revision in configuration identity.
Goals stay unwrapped; zero-timeout tasks allocate no deadline store/kernel. This
prerequisite does NOT enable positive admission, install an expiry observer, or
connect final assessment/terminal settlement. Positive-duration gates stay closed.
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
                 graph_catalog,limits=Limits(),activity_authorize=None,cleanup_authorize=None,timing_policy=None,worker_process_policy=None,domain_tools=None,provider_decorator=None):
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
        from .domain_tools import DomainTools
        if domain_tools is not None and type(domain_tools) is not DomainTools:raise ValueError("Exact domain tool bundle required")
        if provider_decorator is not None and not callable(provider_decorator):raise ValueError("Explicit provider decorator required")
        self._domain=self._domain_pin=domain_tools
        self._decorate=self._decorate_pin=provider_decorator
        self.executor=executor;self.providers=providers;self.transcript=transcript
        self._prompts=deepcopy(prompts);self._catalog=deepcopy(graph_catalog)
        self._revision=policy_revision;self._current=current;self._guard=lifecycle_guard
        self._authorize=authorize;self._assets=asset_authorize;self._input=validate_input;self._limits=limits
        self._pins=(executor,providers,transcript,providers.tasks,providers.agents,providers.profiles,providers.factory)
        if activity_authorize is not None and not callable(activity_authorize):raise ValueError('Explicit progress projection authorization required')
        if cleanup_authorize is not None and not callable(cleanup_authorize):raise ValueError('Explicit cleanup policy required')
        if timing_policy is not None:
            from .task_timing import TaskTimingPolicy
            if type(timing_policy) is not TaskTimingPolicy:raise ValueError('Exact task timing policy required')
            timing_policy.__post_init__()
            if cleanup_authorize is None:raise ValueError('Task timing requires explicit inflight cleanup policy')
        if worker_process_policy is not None:
            from .task_process import WorkerProcessPolicy
            if type(worker_process_policy) is not WorkerProcessPolicy or cleanup_authorize is None:
                raise ValueError('Exact process policy and cleanup authority required')
            worker_process_policy.__post_init__()
        self._process_policy=self._process_pin=worker_process_policy
        self._process_fields=None if worker_process_policy is None else tuple(vars(worker_process_policy).items())
        self._timing_policy=self._timing_pin=timing_policy
        self._timing_fields=None if timing_policy is None else (timing_policy.revision,timing_policy.clock,
            timing_policy.wall_clock,timing_policy.stamp_authorize,timing_policy.dispatch_authorize)
        self._cleanup_authorize=cleanup_authorize;self._cleanup_pin=cleanup_authorize
        self._activity_authorize=activity_authorize;self._activity_pin=activity_authorize
        self._configurations={}
        self._timed_admission=None;self._admission_pin=None;self._admission_digest=None
        self._transcript_store=transcript.store
        self._transcript_identity=(transcript.store.root,transcript.store.max_bytes,transcript.agent_id,transcript.recovery_guard)
        self._policy_pins=(current,lifecycle_guard,authorize,asset_authorize,validate_input,limits,policy_revision)
        self._configuration_bytes=self._configuration_identity()
        self.coordinator=BootstrapCoordinator(BootstrapJournal(executor),planner_factory=self._goals,
            lifecycle_guard=lifecycle_guard,current=self._bootstrap_current)
        self._coordinator=self.coordinator
        self._coordinator_pins=(self.coordinator.journal,self.coordinator.factory,self.coordinator.current)
    def _configuration_identity(self):
        payload={'prompts':self._prompts,'catalog':{k:[asdict(r) for r in v] for k,v in self._catalog.items()}}
        if self._domain is not None:payload['domain_tools']={'revision':self._domain.revision,'catalog':[asdict(r) for r in self._domain.catalog]}
        if self._decorate is not None:payload['provider_decorator']=True
        if self._process_policy is not None:payload['worker_process']=self._process_policy.configuration()
        if self._timing_policy is not None:payload['task_timing']={'enabled':True,'revision':self._timing_policy.revision}
        if self._timed_admission is not None:payload['timed_admission']=self._admission_digest
        return json.dumps(payload,sort_keys=True,allow_nan=False)
    def bind_timed_admission(self,capability):
        from .timed_admission import PreparedTimedAdmission,validate_issued_admission
        self.validate_bindings()
        if (type(capability) is not PreparedTimedAdmission or capability.runtime is not self
            or self._timed_admission is not None or self._configurations):
            raise RuntimeUnavailable('Timed admission requires unused original runtime')
        validate_issued_admission(capability,self)
        self._timed_admission=self._admission_pin=capability
        self._admission_digest=capability.digest
        self._configuration_bytes=self._configuration_identity()
        self.validate_bindings();return True
    def validate_bindings(self):
        e,p,t,tasks,agents,profiles,factory=self._pins
        if self._domain is not self._domain_pin or self._decorate is not self._decorate_pin:raise RuntimeUnavailable("Domain bindings changed")
        if self._timed_admission is not self._admission_pin:raise RuntimeUnavailable('Timed admission binding changed')
        if self._timed_admission is not None:
            from .timed_admission import validate_issued_admission
            validate_issued_admission(self._timed_admission,self)
            if self._timed_admission.digest!=self._admission_digest:raise RuntimeUnavailable('Timed admission identity changed')
        if self._process_policy is not self._process_pin or self._process_policy is not None and any(getattr(self._process_policy,k) is not v for k,v in self._process_fields):
            raise RuntimeUnavailable('Process policy changed')
        policy=self._timing_policy
        if policy is not self._timing_pin:raise RuntimeUnavailable('Task timing policy changed')
        if policy is not None:
            fields=(policy.revision,policy.clock,policy.wall_clock,policy.stamp_authorize,policy.dispatch_authorize)
            if any(a is not b for a,b in zip(fields,self._timing_fields)):
                raise RuntimeUnavailable('Task timing authority changed')
        if self._cleanup_authorize is not self._cleanup_pin:raise RuntimeUnavailable('Cleanup policy changed')
        if self._activity_authorize is not self._activity_pin:raise RuntimeUnavailable('Activity projection authority changed')
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
    @staticmethod
    def _timeout_value(value):
        if type(value) is not int or not 0<=value<=2**31-1:
            raise RuntimeUnavailable('Timeout must be an exact bounded nonnegative integer')
        return value
    def _task_timeout(self,task):
        """Read configuration under the resolver's existing task-read authority.

        Provisional options have no persisted ID. Persisted snapshots cannot
        override the database or invent a default for an invalid/missing value.
        Zero retains the historical digest; positive identity needs explicit
        timing composition, never permission to admit or execute a task.
        """
        self.validate_bindings();resolver=self.providers
        snapshot=resolver._snapshot(task)
        if snapshot.get('provisional') is True:
            value=self._timeout_value(snapshot['options'].get('timeout_seconds',0))
            if 'timeout_seconds' in snapshot and self._timeout_value(snapshot['timeout_seconds'])!=value:
                raise RuntimeUnavailable('Ambiguous provisional timeout')
        else:
            if snapshot.get('options') is not None:raise RuntimeUnavailable('Persisted timeout cannot have provisional options')
            pins=resolver._pins();guard=resolver._resources[5]
            resolver._resources_check(pins)
            with guard(deepcopy(snapshot)):
                resolver._grant(pins[1],snapshot,pins,'goals')
                row=resolver.tasks.get(snapshot['task_id'])
                resolver._grant(pins[1],snapshot,pins,'goals')
                if row is None or row.id!=snapshot['task_id'] or row.exploration_id!=snapshot['exploration_id']:
                    raise RuntimeUnavailable('Persisted timeout task unavailable')
                value=self._timeout_value(getattr(row,'timeout_seconds',None))
                row_identity=(row.id,row.exploration_id,value)
                if 'timeout_seconds' in snapshot and self._timeout_value(snapshot['timeout_seconds'])!=value:
                    raise RuntimeUnavailable('Persisted timeout snapshot changed')
            resolver._grant(pins[1],snapshot,pins,'goals')
            # A host grant may mutate the returned Task object after capture.
            # Reject that alias drift without pretending this is an atomic DB
            # snapshot across separate model-selection reads. No retry loop.
            if (type(row.id) is not int or type(row.exploration_id) is not int
                or (row.id,row.exploration_id,self._timeout_value(getattr(row,'timeout_seconds',None)))!=row_identity):
                raise RuntimeUnavailable('Persisted timeout row changed after authorization')
        self.validate_bindings()
        if value and self._timing_policy is None:raise RuntimeUnavailable('Positive timeout configuration requires explicit timing policy')
        return value

    def _select(self,task):
        self.validate_bindings()
        task=deepcopy(task);timeout=self._task_timeout(task)
        models={role:self.providers.select(deepcopy(task),role).model for role in ('goals','planner','worker')}
        payload={'models':{k:asdict(v) for k,v in models.items()},'prompts':self._prompts,
                 'catalog':{k:[asdict(r) for r in v] for k,v in self._catalog.items()},
                 'limits':asdict(self._limits),'policy':self._revision,'activity_projection':self._activity_authorize is not None}
        if self._cleanup_authorize is not None:payload['inflight_cleanup']=True
        if self._domain is not None:payload['domain_tools']={'revision':self._domain.revision,'catalog':[asdict(r) for r in self._domain.catalog]}
        if self._decorate is not None:payload['provider_decorator']=True
        if self._process_policy is not None:payload['worker_process']=self._process_policy.configuration()
        if self._timing_policy is not None:payload['task_timing']={'enabled':True,'revision':self._timing_policy.revision}
        if self._timed_admission is not None:payload['timed_admission']=self._admission_digest
        if timeout:payload['timeout_seconds']=timeout
        if self._task_timeout(task)!=timeout:raise RuntimeUnavailable('Timeout changed during role selection')
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
        projector=None
        if self._activity_authorize is not None:
            from .activity_projection import ActivityProjector
            projector=ActivityProjector(self.executor,scope,transcript=self.transcript,current=current,
                lifecycle_guard=guard,authorize=self._activity_authorize)
        return GoalPlanner(store=ExplorationStore(self.executor,scope.exploration_id),transcript=self.transcript,
            current=current,lifecycle_guard=guard,provider_resolver=lambda s,m:self._binding(s,'goals',m,current,guard),
            authorize=self._authorize,validate_input=self._input,limits=self._limits,activity_projector=projector)
    def _binding(self,scope,role,model,current,guard):
        binding=self.providers.bind(scope,role,model,current=current,lifecycle_guard=guard)
        return binding if self._decorate is None else self._decorate(scope,role,binding)
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
        def cleanup_current(s):
            self.validate_bindings()
            if (s.task_id,s.exploration_id,s.run_id,s.revision)!=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision):return False
            if self._current(scope.task_id,scope.exploration_id,self._revision) is not True:return False
            _,live=self._select({'task_id':scope.task_id,'exploration_id':scope.exploration_id})
            result=live==scope.revision and self._current(scope.task_id,scope.exploration_id,self._revision) is True
            self.validate_bindings();return result
        def guarded(s):
            if (s.task_id,s.exploration_id,s.run_id,s.revision)!=(scope.task_id,scope.exploration_id,scope.run_id,scope.revision):raise RuntimeUnavailable('Foreign runtime scope')
            return guard(scope)
        process=None
        if self._process_policy is not None:
            from .task_process import PreparedWorkerProcess
            process=PreparedWorkerProcess(scope,self._process_policy,configuration_current=cleanup_current)
        def graph_tools(role,s,check):
            gs=GraphScope(s.task_id,s.exploration_id,s.run_id,s.revision,role,getattr(s,'intent_id',0))
            tools=GraphTools(graph,gs,current=lambda _:check(),lifecycle_guard=lambda _:guarded(s),authorize=self._authorize,asset_authorize=lambda scope,ids:self._assets(scope,ids,None)).tools()
            if role=='worker' and process is not None:tools=tools+process.tools(s)
            if role=='worker' and self._domain is not None:tools=tools+self._domain.tools(s,check,lambda:guarded(s))
            names={r.key for r in self._catalog[role]}
            if role=='worker' and self._domain is not None:names.update(r.key for r in self._domain.catalog)
            if not names.issubset({t.name for t in tools}):raise RuntimeUnavailable('Only explicit local graph tools supported')
            return tuple(t for t in tools if t.name in names)
        timing=None;clock_options={}
        if self._timing_policy is not None:
            from .task_timing import PreparedTaskTiming
            timing=PreparedTaskTiming(scope,self._timing_policy,configuration_current=cleanup_current)
            clock_options={'clock':self._timing_policy.clock}
        def bind_role(s,role,model):
            self.validate_bindings()
            binding=self._binding(s,role,model,exact,guarded)
            self.validate_bindings()
            return timing.wrap(s,role,binding) if timing is not None else binding
        ps=PlannerScope(scope.task_id,scope.exploration_id,scope.run_id,scope.revision,models['planner'],self._prompts['planner'])
        planner=PlannerCycle(store=graph,transcript=self.transcript,current=exact,lifecycle_guard=guarded,
            provider_resolver=lambda s,m:bind_role(s,'planner',m),
            authorize=self._authorize,asset_authorize=self._assets,limits=self._limits,
            additional_tools=graph_tools('planner',ps,lambda:exact(ps)),additional_catalog=self._catalog['planner'],**clock_options)
        worker=None
        def registry(s,record):
            def owned():
                worker.check(s,'tool',claimed=True);return True
            reg=ToolRegistry('worker',handlers=graph_tools('worker',s,owned),current=lambda revision:revision==scope.revision and exact(s),
                lifecycle_guard=lambda _:guarded(s),authorize=self._authorize)
            reg.reload(tuple(self._catalog['worker'])+(() if self._domain is None else self._domain.catalog),scope.revision);return reg
        worker=IntentWorker(store=WorkerStore(self.executor,scope.task_id,scope.exploration_id,
            lifetime_guard=lambda tid,eid,write:guard(scope)),transcript=self.transcript,
            current=lambda s,phase:exact(s),lifecycle_guard=guarded,
            provider_resolver=lambda s,m:bind_role(s,'worker',m),
            registry_factory=registry,authorize=self._authorize,limits=self._limits,**clock_options)
        projector=None
        if self._activity_authorize is not None:
            from .activity_projection import ActivityProjector
            projector=ActivityProjector(self.executor,scope,transcript=self.transcript,current=exact,lifecycle_guard=guarded,authorize=self._activity_authorize)
        return TaskEngineHandle(scope,token,store=graph,planner=planner,planner_scope=ps,worker=worker,
            worker_model=models['worker'],worker_system=self._prompts['worker'],owner='runtime-'+str(scope.run_id),
            current=current,lifecycle_guard=guard,single_process=True,activity_projector=projector,cleanup_authorize=self._cleanup_authorize,
            cleanup_current=cleanup_current if self._cleanup_authorize is not None else None,process=process,**({'timing':timing} if timing is not None else {}))
    @staticmethod
    def initialized_readiness(task):raise RuntimeUnavailable('Initialized task adoption requires explicit recovery and is unsupported')
    @staticmethod
    def initialized_factory(*args):raise RuntimeUnavailable('Initialized task adoption is unsupported')
