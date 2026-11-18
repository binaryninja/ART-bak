"""Opt-in task LLM routes, actual scoped stores, no provider construction.

Source server/task_resolution.go order: valid role binding, explicit task chain
(no fallback if exhausted/invalid), stored global profile, explicit environment.
`available` means host-confirmed static configuration ONLY, never connectivity,
quota, permission to call a model, or provider readiness. Configuration validator
is trusted pure host code. Environment resolver must be explicit; None means
confirmed absent. DTOs whitelist name/format/model/id and never credentials/URLs.

PUT is narrower than source: host-confirmed idle and SQL nonlive lifecycle only;
quota-blocked created intents reject rather than silently skip reopen. Requires
coordinate(binding, change, write): it must preflight cache/audit/revision handling,
invoke write EXACTLY ONCE, then synchronize caches and record any manual-switch
audit before returning the exact ChainReceipt object from write. Missing adapter
is 503 before writes. Failure after write is committed/needs_reconciliation, never
replayed automatically. Guard must fence admission, role/profile/config edits and
cache readers. No default registration or implicit global/environment selection.
Source switch_event DTO is not projected here; coordinator must retain its audit.
"""
from dataclasses import dataclass
from threading import get_ident
from types import MethodType
import json,re
from urllib.parse import urlsplit
from .core import json_response,error_response
from ..configuration_owner import ConfigurationOwnershipError
from ..db.configuration_ownership import require_write
from ..db.core import int64
from ..db.tasks import TaskStore,TaskInvalid
from ..db.transaction_coordination import TransactionCoordinationBinding
from ..db.profiles import ProfileStore
from ..db.agents import AgentStore

_TASK_GET = TaskStore.get
_TASK_HYDRATE = TaskStore._hydrate
_PROFILE_GET = ProfileStore.get
_PROFILE_ROWS = ProfileStore._rows
_TASK_REPLACE = TaskStore.replace_llm_profiles_idle


@dataclass(frozen=True)
class TaskProfileBinding:
    task_id:int
    exploration_id:int
    revision:str

@dataclass(frozen=True)
class ChainChange:
    profile_ids:tuple[int,...]
    active_profile_id:int=0

@dataclass(frozen=True)
class ChainReceipt:
    task_id:int
    revision:int
    profile_ids:tuple[int,...]
    active_profile_id:int|None

@dataclass(frozen=True)
class EnvironmentSelection:
    name:str
    format:str
    model:str
    configured:bool

class AccessDenied(RuntimeError):pass
class Unavailable(RuntimeError):pass
class Conflict(RuntimeError):pass
class Missing(LookupError):pass

def _pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('Duplicate field')
        out[k]=v
    return out

def _parse(body):
    obj=json.loads(body.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    if type(obj) is not dict or set(obj)-{'llm_profile_ids','active_llm_profile_id'}:raise ValueError('Invalid chain request')
    ids=obj.get('llm_profile_ids',[]);active=obj.get('active_llm_profile_id') or 0
    if type(ids) is not list or len(ids)>100:raise ValueError('Bounded chain required')
    for id in ids:
        if int64(id,'profile ID')<=0:raise ValueError('Positive profile ID required')
    if len(set(ids))!=len(ids):raise ValueError('Duplicate profile')
    if obj.get('active_llm_profile_id') is not None and type(obj['active_llm_profile_id']) is not int:raise ValueError('Invalid active profile')
    if int64(active,'active profile ID')<0 or (ids and active and active not in ids):raise ValueError('Active profile outside chain')
    return ChainChange(tuple(ids),active)

class TaskProfileService:
    def __init__(self,tasks,profiles,agents,*,resolve,authorize,profile_authorize,current,lifecycle_guard,configuration_valid,environment,mutation_authorize=None,idle=None,coordinate=None,transaction_coordination=None):
        if not isinstance(tasks,TaskStore) or not isinstance(profiles,ProfileStore) or not isinstance(agents,AgentStore) or tasks.executor is not profiles.executor or tasks.executor is not agents.executor:raise ValueError('Actual stores on one executor required')
        if any(not callable(f) for f in (resolve,authorize,profile_authorize,current,lifecycle_guard,configuration_valid,environment)):raise ValueError('Explicit resolution and authorization policies required')
        if any(f is not None and not callable(f) for f in (mutation_authorize,idle,coordinate)):raise ValueError('Invalid mutation adapters')
        self.tasks,self.profiles,self.agents=tasks,profiles,agents;self._executor=tasks.executor
        self.transaction_coordination = transaction_coordination
        self._original_coordination = transaction_coordination
        if transaction_coordination is not None:
            if (type(transaction_coordination) is not TransactionCoordinationBinding
                    or transaction_coordination.executor is not tasks.executor
                    or type(tasks) is not TaskStore or type(profiles) is not ProfileStore
                    or type(agents) is not AgentStore):
                raise ValueError('Coordination requires the original stores on the bound executor')
            self._original_stores = (tasks, profiles, agents)
            self._check_writer()
        self.resolve,self.authorize,self.profile_authorize,self.current,self.guard=resolve,authorize,profile_authorize,current,lifecycle_guard
        self.valid,self.environment=configuration_valid,environment
        self.mutate,self.idle,self.coordinate=mutation_authorize,idle,coordinate
    def register(self,router):
        router.add('PUT','/api/tasks/{id}/llm',lambda r,p:self.handle('replace',r,p))
        router.add('GET','/api/tasks/{id}/llm/resolution',lambda r,p:self.handle('resolution',r,p))
    def _check_writer(self):
        option = self._original_coordination
        if self.transaction_coordination is not option:
            raise Unavailable()
        if option is not None:
            stores = self.tasks, self.profiles, self.agents
            if (self._executor is not option.executor
                    or any(store is not original or store.executor is not option.executor
                           for store, original in zip(stores, self._original_stores))):
                raise Unavailable()
            for store, original in ((self.tasks, _TASK_GET), (self.tasks, _TASK_HYDRATE),
                                    (self.profiles, _PROFILE_GET), (self.profiles, _PROFILE_ROWS),
                                    (self.tasks, _TASK_REPLACE)):
                method = getattr(store, original.__name__, None)
                if type(method) is not MethodType or method.__self__ is not store or method.__func__ is not original:
                    raise Unavailable()

    def _check_admitted_writer(self):
        self._check_writer()
        if self._original_coordination is not None:
            try:
                require_write(self._original_coordination.executor, 'task_profiles.replace_idle')
            except ConfigurationOwnershipError as error:
                raise Unavailable() from error

    def _check(self,binding, *, writer=False):
        if writer:
            self._check_admitted_writer()
        if any(s.executor is not self._executor for s in (self.tasks,self.profiles,self.agents)):raise Unavailable()
        if self.current(binding) is not True:raise Conflict()
        if writer:
            self._check_admitted_writer()
        if any(s.executor is not self._executor for s in (self.tasks,self.profiles,self.agents)):raise Unavailable()
    def _policy(self,fn,*args):
        if not callable(fn):raise Unavailable()
        if fn(*args) is not True:raise AccessDenied()
    def _profile(self,request,binding,id,source, *, writer=False):
        self._policy(self.profile_authorize,request,binding,id);self._check(binding, writer=writer)
        if writer and self._original_coordination is not None:
            profile = _PROFILE_GET(self._original_stores[1], id)
        else:
            profile = self.profiles.get(id)
        self._check(binding, writer=writer)
        result=dict(name='',format='',model='',source=source,available=False)
        if profile is None:result['reason']='LLM profile does not exist';return result
        result.update(profile_id=profile.id,name=profile.name,format=profile.format,model=profile.model)
        if not profile.api_key:result['reason']='LLM profile has no API key';return result
        valid=self.valid(profile);self._check(binding, writer=writer)
        if type(valid) is not bool:raise Unavailable()
        if valid:result['available']=True
        else:result['reason']='LLM profile configuration is invalid'
        return result
    def _resolution(self,request,binding,task):
        result={}
        for role in ('mainagent','planner','worker'):
            agent=self.agents.get(role);self._check(binding)
            resolved=None
            if agent is not None and agent.llm_profile_id is not None:
                selected=self._profile(request,binding,agent.llm_profile_id,'agent_binding')
                if selected['available']:resolved=selected
            if resolved is None and task.llm_profile_ids:
                resolved=(self._profile(request,binding,task.active_llm_profile_id,'task_chain') if task.active_llm_profile_id is not None else dict(name='',format='',model='',source='task_chain',available=False,reason='Task LLM profile chain is quota exhausted'))
            if resolved is None:
                # Read just the ID before profile authorization, never fetch a
                # private active profile merely to discover which one is active.
                from ..db.core import Query
                rows=self._executor.fetch_all(Query('SELECT id FROM llm_profiles WHERE is_default LIMIT 1',()))
                self._check(binding)
                if rows:
                    selected=self._profile(request,binding,rows[0][0],'global_profile')
                    if selected['available']:resolved=selected
            if resolved is None:
                env=self.environment(request,binding);self._check(binding)
                if env is not None:
                    if not isinstance(env,EnvironmentSelection) or type(env.configured) is not bool or any(not isinstance(v,str) or len(v)>4096 for v in (env.name,env.format,env.model)):raise Unavailable()
                    if env.configured:resolved=dict(name=env.name or 'Global configuration',format=env.format,model=env.model,source='environment',available=True)
                if resolved is None:resolved=dict(name='',format='',model='',source='global',available=False,reason='No configured LLM source')
            result[role]=resolved
        return result
    def handle(self,action,request,params):
        committed=False;write_attempted=False
        try:
            raw=params['id']
            if not isinstance(raw,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',raw):raise ValueError()
            id=int64(int(raw),'task ID')
            if action not in ('replace','resolution') or urlsplit(request.target).query:raise ValueError()
            if len(request.body)>65536:return error_response(413,'Request too large')
            change=_parse(request.body) if action=='replace' else None
            if action=='resolution' and request.body:raise ValueError()
            if action=='replace' and any(not callable(f) for f in (self.mutate,self.idle,self.coordinate)):raise Unavailable()
            writer = action == 'replace'
            option = self._original_coordination if writer else None
            if writer:
                self._check_writer()
            task_store = self._original_stores[0] if option is not None else None
            response = None
            binding=self.resolve(request,id)
            if option is not None:
                self._check_writer()
            if binding is None:raise Missing()
            if not isinstance(binding,TaskProfileBinding) or binding.task_id!=id or type(binding.task_id) is not int or int64(binding.exploration_id,'exploration ID')<=0 or not isinstance(binding.revision,str) or not binding.revision or len(binding.revision)>256:raise Unavailable()
            with self.guard(binding):
                self._check(binding, writer=writer);self._policy(self.authorize,request,action,binding,change);self._check(binding, writer=writer)
                task = self.tasks.get(id) if option is None else _TASK_GET(task_store, id)
                self._check(binding, writer=writer)
                if task is None:raise Missing()
                if task.exploration_id!=binding.exploration_id:raise Conflict()
                if action=='resolution':return json_response(200,self._resolution(request,binding,task))
                self._policy(self.mutate,request,binding,change);self._check(binding, writer=writer)
                if self.idle(binding) is not True:raise Conflict()
                if option is not None:
                    self._check_admitted_writer()
                for profile in change.profile_ids:
                    if not self._profile(request,binding,profile,'task_chain', writer=True)['available']:raise ValueError()
                self._check(binding, writer=writer);receipt=None;attempted=False;write_calls=0;write_active=True;write_thread=get_ident()
                def write():
                    nonlocal receipt,committed,attempted,write_attempted,write_calls
                    if not write_active or get_ident()!=write_thread:raise Unavailable()
                    write_calls+=1
                    if attempted:raise Unavailable()
                    attempted=True;self._check(binding, writer=writer)
                    if self.idle(binding) is not True:raise Conflict()
                    self._check(binding, writer=writer)
                    args = (id, binding.exploration_id, task.llm_chain_revision,
                            list(change.profile_ids), change.active_profile_id)
                    write_attempted=True
                    if option is None:
                        revision = self.tasks.replace_llm_profiles_idle(*args)
                    else:
                        revision = _TASK_REPLACE(task_store, *args, transaction_coordination=option)
                    committed=True
                    receipt=ChainReceipt(id,revision,change.profile_ids,(change.active_profile_id or change.profile_ids[0]) if change.profile_ids else None)
                    return receipt
                try:returned=self.coordinate(binding,change,write)
                finally:write_active=False
                self._check(binding, writer=writer)
                if receipt is None or returned is not receipt or write_calls!=1 or any(s.executor is not self._executor for s in (self.tasks,self.profiles,self.agents)):raise Unavailable()
                response = json_response(200,dict(id=str(id),llm_profile_ids=list(receipt.profile_ids),active_llm_profile_id=receipt.active_profile_id,llm_failover_state='ready' if receipt.profile_ids else 'default',reopened_intents=0))
                if option is None:
                    return response
            if option is not None:
                self._check_writer()
                if response is None:
                    raise Unavailable()
                return response
        except Exception as error:
            if committed:return json_response(503,dict(error='Profile chain committed; runtime synchronization requires reconciliation',committed=True,needs_reconciliation=True))
            if write_attempted and not isinstance(error,TaskInvalid):return json_response(503,dict(error='Profile chain write outcome uncertain; reconcile before retrying',committed=None,needs_reconciliation=True))
            if isinstance(error,TaskInvalid):return error_response(409,'Task lifecycle, profile chain or revision changed')
            if isinstance(error,AccessDenied):return error_response(403,'Task profile access denied')
            if isinstance(error,Missing):return error_response(404,'task not found')
            if isinstance(error,Conflict):return error_response(409,'Task lifecycle or configuration changed')
            if isinstance(error,Unavailable):return error_response(503,'Task profile capability unavailable')
            if isinstance(error,(ValueError,TypeError,UnicodeError,TaskInvalid)):return error_response(400,'Invalid task profile request')
            return error_response(500,'Task profile operation failed; reconcile before retrying mutations')
