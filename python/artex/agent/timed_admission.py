"""Explicit manual-observer admission candidate; no ambient readiness authority.

Requires both code-owned PostgreSQL contract validators. Missing/unsupported source
validation refuses setup. Catalog observations supplied by callers are never used.
No DDL, timer, provider call or fallback plan is performed here.
"""
from dataclasses import dataclass,fields,asdict
from copy import deepcopy
from collections.abc import Mapping
import hashlib,json
from weakref import WeakKeyDictionary,ref
from threading import RLock
from ..db.core import Query
from ..manager import RuntimeUnavailable
from ..db.timed_schema import validate_timed_schema as _EXTENSION_VALIDATOR
try:
    from ..db.timed_source_schema import validate_timed_source_schema as _SOURCE_VALIDATOR
except ImportError:
    _SOURCE_VALIDATOR=None

_KEY=object()
_ISSUED=WeakKeyDictionary()
_ISSUANCE_LOCK=RLock()

@dataclass(frozen=True)
class TimedAdmissionRequest:
    expected_database:str
    mode:str='manual-observer'
    def __post_init__(self):
        if type(self.expected_database) is not str or not 1<=len(self.expected_database.encode())<=256 or '\0' in self.expected_database or self.mode!='manual-observer':
            raise ValueError('Explicit database and manual-observer mode required')


def _same_schema(actual, expected):
    """Compare builtin JSON shape without Python bool/int equality coercion."""
    if type(actual) is not type(expected):return False
    if type(expected) is dict:
        return (all(type(key) is str for key in actual) and actual.keys()==expected.keys()
                and all(_same_schema(actual[key],value) for key,value in expected.items()))
    if type(expected) is list:
        return len(actual)==len(expected) and all(_same_schema(a,b) for a,b in zip(actual,expected))
    return actual==expected


def _validate_graph_catalog(catalog):
    from ..db.tool_catalog import CatalogTool,validate
    from ..tools.graph import SCHEMAS
    if type(catalog) is not dict or set(catalog)!= {'planner','worker'}:
        raise RuntimeUnavailable('Explicit local role catalogs required')
    for role,rows in catalog.items():
        if type(rows) not in (list,tuple) or len(rows)>len(SCHEMAS):raise RuntimeUnavailable('Bounded native graph catalog required')
        seen=set()
        for row in rows:
            if type(row) is not CatalogTool:raise RuntimeUnavailable('Exact native catalog row required')
            validate(row,custom=False)
            if (row.key not in SCHEMAS or row.key in seen or not _same_schema(row.schema,SCHEMAS[row.key])
                or row.agents!=[role] or row.system is not True or row.enabled is not True
                or row.kind!='builtin' or row.exec or row.deferred is not False):
                raise RuntimeUnavailable('Only exact explicitly bound native graph tools supported')
            seen.add(row.key)


def _observation_bytes(observations,database):
    from ..db import timed_schema,timed_source_schema
    if type(observations) is not tuple or len(observations)!=2:
        raise RuntimeUnavailable('Original schema observations required')
    contracts=((timed_schema.TimedSchemaObservation,'extension-v1',timed_schema._MANIFEST_SHA,timed_schema._TABLES),
               (timed_source_schema.TimedSourceObservation,'task-runtime-v1',timed_source_schema._BASELINE_SHA,timed_source_schema._TABLES))
    for observed,(kind,profile,manifest,tables) in zip(observations,contracts):
        if (type(observed) is not kind or observed.database!=database or observed.schema!='public'
            or type(observed.server_major) is not int or observed.server_major!=17
            or observed.profile!=profile or observed.manifest_digest!=manifest
            or type(observed.tables) is not tuple or observed.tables!=tables
            or observed.source_readiness is not False
            or type(observed.descriptor_digest) is not str or len(observed.descriptor_digest)!=64
            or any(c not in '0123456789abcdef' for c in observed.descriptor_digest)):
            raise RuntimeUnavailable('Unexpected original schema observation')
    if observations[1].modes!=('local-goals-planner-worker','cleanup','clock','final-lifecycle'):
        raise RuntimeUnavailable('Unsupported source schema modes')
    return json.dumps([asdict(item) for item in observations],sort_keys=True,separators=(',',':'),allow_nan=False)


def prepare_timed_admission(runtime,policy,request):
    """Validate actual catalog contracts, then issue one unbound application cap."""
    from .task_runtime import TaskRuntimeAssembly
    from .timed_lifecycle import TimedLifecyclePolicy
    from .final_task_provider import FinalTaskProviderAdapter
    if type(runtime) is not TaskRuntimeAssembly or type(policy) is not TimedLifecyclePolicy or type(request) is not TimedAdmissionRequest:
        raise ValueError('Exact runtime/lifecycle/admission request required')
    request.__post_init__();policy.validate_runtime(runtime)
    plan=policy.final_plan
    if (plan is None or type(plan.task_provider) is not FinalTaskProviderAdapter
        or plan.task_provider.resolver is not runtime.providers or plan.provider_resolver is not None
        or plan.clock is not policy.timing_policy.clock):
        raise RuntimeUnavailable('Original persisted final planner and clock required')
    # First candidate has an explicit finite source contract, not all plugin paths.
    if runtime._process_policy is not None or runtime._activity_authorize is not None:
        raise RuntimeUnavailable('Additional process/activity readiness unsupported')
    _validate_graph_catalog(runtime._catalog)
    try:
        from ..db.timed_schema import validate_timed_schema
        from ..db.timed_source_schema import validate_timed_source_schema
    except ImportError:
        raise RuntimeUnavailable('Complete source schema validator unavailable') from None
    if validate_timed_schema is not _EXTENSION_VALIDATOR or _SOURCE_VALIDATOR is None or validate_timed_source_schema is not _SOURCE_VALIDATOR:
        raise RuntimeUnavailable('Original source and extension validators required')
    from ..db.postgres import PostgresExecutor
    if type(runtime.executor) is not PostgresExecutor or runtime.executor._owns_connection is not True or runtime.executor._closed:
        raise RuntimeUnavailable('Exclusive owned PostgreSQL session required; pools unsupported')
    cap=PreparedTimedAdmission(_KEY,runtime,policy,request,validate_timed_schema,validate_timed_source_schema)
    _ORIGINAL_METHODS['_validate_schema'](cap)
    _ORIGINAL_METHODS['_pins_only'](cap)
    with _ISSUANCE_LOCK:_ISSUED[cap]=(ref(runtime),cap.digest)
    validate_issued_admission(cap,runtime)
    runtime.bind_timed_admission(cap)
    return cap


class PreparedTimedAdmission:
    def __init__(self,key,runtime,policy,request,extension_validator,source_validator):
        if key is not _KEY:raise ValueError('Admission must be issued by verified setup')
        self.runtime=runtime;self.policy=policy;self.request=request
        self._pins=(runtime,policy,request,runtime.executor,runtime.providers,runtime.coordinator)
        self._policy_fields=tuple(getattr(policy,f.name) for f in fields(policy))
        self._plan=policy.final_plan;self._plan_fields=tuple(getattr(self._plan,f.name) for f in fields(self._plan))
        self._final_digest=self._plan.configuration.digest
        self._request_values=(request.expected_database,request.mode)
        self._validators=(extension_validator,source_validator)
        self._session=(runtime.executor._connection,runtime.executor._cursor_factory)
        self._methods=(runtime.bootstrap_configuration,runtime.providers.select)
        self._application=None;self._application_pins=None;self._bound=False
        self._schema_contract=None
        self._schema_observations=None
        self._digest_payload={'mode':request.mode,'database':request.expected_database,
            'extension':'extension-v1','source':'task-runtime-v1','lifecycle_revision':policy.revision,
            'final_configuration':self._final_digest}
        self.digest=hashlib.sha256(json.dumps(self._digest_payload,sort_keys=True).encode()).hexdigest()
        self._digest=self.digest
    def _pins_only(self):
        runtime,policy,request,executor,providers,coordinator=self._pins
        if (self.runtime is not runtime or self.policy is not policy or self.request is not request
            or runtime.executor is not executor or runtime.providers is not providers or runtime.coordinator is not coordinator
            or self.digest!=self._digest or (request.expected_database,request.mode)!=self._request_values
            or any(getattr(policy,f.name) is not v for f,v in zip(fields(policy),self._policy_fields))
            or policy.final_plan is not self._plan or self._plan.configuration.digest!=self._final_digest
            or any(getattr(self._plan,f.name) is not v for f,v in zip(fields(self._plan),self._plan_fields))
            or (runtime.bootstrap_configuration,runtime.providers.select)!=self._methods):
            raise RuntimeUnavailable('Timed admission configuration changed')
        if executor._connection is not self._session[0] or executor._cursor_factory is not self._session[1] or executor._owns_connection is not True or executor._closed:
            raise RuntimeUnavailable('Original exclusively owned PostgreSQL session required')
        self._plan.task_provider.validate_bindings()
        payload={'mode':request.mode,'database':request.expected_database,
            'extension':'extension-v1','source':'task-runtime-v1','lifecycle_revision':policy.revision,
            'final_configuration':self._final_digest}
        if self._schema_contract is not None:
            payload['schema_contract']=self._schema_contract
            if self._schema_observations is None or _observation_bytes(self._schema_observations,self._request_values[0])!=self._schema_contract:
                raise RuntimeUnavailable('Original schema observations changed')
        if hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()!=self.digest:
            raise RuntimeUnavailable('Timed admission schema identity changed')
        return True
    def _validate_schema(self):
        self._pins_only()
        from ..db import timed_schema,timed_source_schema
        if (timed_schema.validate_timed_schema,timed_source_schema.validate_timed_source_schema)!=self._validators:
            raise RuntimeUnavailable('Original schema validators changed')
        observations=[]
        for validator,profile in zip(self._validators,('extension-v1','task-runtime-v1')):
            observations.append(validator(self._pins[3],expected_database=self._request_values[0],expected_schema='public',profile=profile))
            self._pins_only()
        observed=tuple(observations)
        contract=_observation_bytes(observed,self._request_values[0])
        if self._schema_contract is None:
            self._schema_observations=observed
            self._schema_contract=contract
            self._digest_payload['schema_contract']=contract
            self.digest=hashlib.sha256(json.dumps(self._digest_payload,sort_keys=True).encode()).hexdigest()
            self._digest=self.digest
        elif contract!=self._schema_contract:
            raise RuntimeUnavailable('Timed admission schema contract drift')
        self._pins_only()
    def bind_application(self,application):
        validate_issued_admission(self,self.runtime)
        from ..server.task_runtime_setup import TaskRuntimeApplication
        if type(application) is not TaskRuntimeApplication or self._bound:
            raise RuntimeUnavailable('Original application may be bound once')
        self._pins_only()
        if (application.runtime is not self.runtime or application.manual_actions is None
            or application.timed_lifecycle is None or application.timed_lifecycle.policy is not self.policy
            or application.manual_actions.host is not application.host or application.timed_admission is not self):
            raise RuntimeUnavailable('Complete tracked original lifecycle required')
        self._application=application
        self._application_pins=(application.host,application.manager,application.runner,application.manual_actions,application.timed_lifecycle)
        self._bound=True;self.validate_bindings();return True
    def validate_bindings(self):
        """Pure resource validation: no SQL, model, grant or host callbacks."""
        validate_issued_admission(self,self.runtime)
        a=self._application
        if not self._bound or a is None or a.timed_admission is not self or a.runtime is not self.runtime:
            raise RuntimeUnavailable('Bound timed admission required')
        h,m,r,manual,lifecycle=self._application_pins
        if (a.host is not h or a.manager is not m or a.runner is not r or a.manual_actions is not manual
            or a.timed_lifecycle is not lifecycle or h.manager is not m or h.runner is not r
            or m.runner is not r or m.repository.executor is not self._pins[3]
            or manual.host is not h or manual._application is not a or lifecycle.policy is not self.policy
            or self.runtime._timed_admission is not self):raise RuntimeUnavailable('Timed admission owner changed')
        return True
    def validate_admission_open(self):
        """Admission-only fence; structural cleanup remains valid after sealing."""
        self.validate_bindings()
        manual=self._application_pins[3]
        with manual._condition:
            if manual._sealed:raise RuntimeUnavailable('Manual lifecycle sealed')
        return True
    def validate_task(self,snapshot,*,runner):
        self.validate_admission_open()
        if runner is not self._application_pins[2] or not isinstance(snapshot,Mapping):raise RuntimeUnavailable('Original runner snapshot required')
        task=deepcopy(dict(snapshot));options=task.get('options')
        if options is not None and not isinstance(options,Mapping):
            from ..db.tasks import TaskCreateOptions
            if type(options) is not TaskCreateOptions:raise RuntimeUnavailable('Explicit create options required')
            options=asdict(options)
        source=options if task.get('provisional') is True else task
        if source is None:raise RuntimeUnavailable('Original task options required')
        timeout=source.get('timeout_seconds',0)
        if type(timeout) is not int or not 0<timeout<=2**31-1:raise RuntimeUnavailable('Explicit positive timeout required')
        for values in (task,options or {}):
            if any(values.get(k) for k in ('seed_first_intent','source_task_ids','parent_ref','intercept_rules','first_run_at','deadline_at')):
                raise RuntimeUnavailable('Fresh local unstarted timed bootstrap only')
        self._validate_schema();self.validate_bindings()
        if not task.get('provisional'):
            status=task.get('status')
            if status not in ('created','running') or task.get('paused') or task.get('queued'):
                raise RuntimeUnavailable('Fresh timed generation only')
            raw=task.get('id',task.get('task_id'))
            if isinstance(raw,str) and raw.isascii() and raw.isdecimal():raw=int(raw)
            if type(raw) is not int or raw<=0:raise RuntimeUnavailable('Persisted task required')
            exp=task.get('exploration_id')
            if type(exp) is not int or exp<=0:raise RuntimeUnavailable('Exact task exploration required')
            clocks=self._pins[3].fetch_all(Query('''SELECT first_run_at,deadline_at FROM tasks
WHERE id=$1 AND exploration_id=$2 AND timeout_seconds=$3 AND status=$4
AND NOT paused AND NOT queued AND deleted_at IS NULL AND archived_at IS NULL
AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1)''',(raw,exp,timeout,status)))
            if len(clocks)!=1 or tuple(clocks[0])!=(None,None):raise RuntimeUnavailable('Fresh unstamped timed task required')
            self.validate_bindings()
            rows=self._pins[3].fetch_all(Query('SELECT task_id FROM public.artex_task_bootstrap WHERE task_id=$1',(raw,)))
            if rows:raise RuntimeUnavailable('Timed bootstrap recovery unavailable')
            self.validate_bindings()
        config=self._methods[0](deepcopy(task))
        selected=self._methods[1](deepcopy(task),'planner')
        if selected.model!=self._plan.configuration.model:raise RuntimeUnavailable('Fixed final planner profile mismatch')
        self.validate_bindings();self.runtime.validate_bindings()
        # Config already includes positive timeout and this immutable capability digest.
        if config is None:raise RuntimeUnavailable('Timed bootstrap configuration unavailable')
        self.validate_admission_open()
        return True
    def validate_handle_configuration(self,task,manager,coordinator,policy_revision,model,*,system,request_id):
        self.validate_bindings()
        if manager is not self._application_pins[1] or coordinator is not self._pins[5]:raise RuntimeUnavailable('Original manager/coordinator required')
        self.validate_task(task,runner=self._application_pins[2])
        config=self._methods[0](deepcopy(task))
        if (config.policy_revision,config.model,config.system,config.request_id)!=(policy_revision,model,system,request_id):
            raise RuntimeUnavailable('Timed bootstrap configuration drift')
        self.validate_admission_open();return True


# Preserve actual code-owned method implementations, not instance callbacks.
# Issuance occurs only after both original catalog validators completed; callers
# constructing an exact-type shell (or copying an issued cap) gain no receipt.
_ORIGINAL_METHODS={name:getattr(PreparedTimedAdmission,name) for name in
    ('_pins_only','_validate_schema','bind_application','validate_bindings',
     'validate_admission_open','validate_task','validate_handle_configuration')}

def validate_issued_admission(capability,runtime):
    if type(capability) is not PreparedTimedAdmission:
        raise RuntimeUnavailable('Original issued timed admission required')
    with _ISSUANCE_LOCK:record=_ISSUED.get(capability)
    if record is None or record[0]() is not runtime or getattr(capability,'runtime',None) is not runtime or getattr(capability,'digest',None)!=record[1]:
        raise RuntimeUnavailable('Verified timed admission issuance required')
    for name,original in _ORIGINAL_METHODS.items():
        if (getattr(PreparedTimedAdmission,name,None) is not original
            or getattr(getattr(capability,name,None),'__func__',None) is not original
            or getattr(getattr(capability,name,None),'__self__',None) is not capability):
            raise RuntimeUnavailable('Original timed admission validation required')
    _ORIGINAL_METHODS['_pins_only'](capability)
    return True
