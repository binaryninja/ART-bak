"""Opt-in hosted bootstrap assembly using one actual capacity controller.

No schema install, prompt selection, provider construction, model call, task
creation, automatic resume, poll, thread or pump occurs here. The existing hosted
factory performs only its explicit load_existing startup reads. The caller owns
executor lifetime, optional polling/pump and cooperative shutdown.

bootstrap_configuration(snapshot) is a trusted, pure host preflight: return a
frozen configuration ONLY after validating current provider/profile, installed
journal, transcript/tool/scope policies and supported task features; return None
ONLY for an already initialized task eligible for initialized_readiness. It is
called again at inert handle construction. Policy revision must cover all those
bindings; coordinator.current must reject subsequent changes before work. No
configuration callback is itself a capacity or model/tool authorization grant.

HTTP facade admissions and optional FIFO scheduling use the SAME AdmissionController.
The admission closure is closed until composition completes and checks exact
identities on every use. No custom admission callback or second manager/host is
accepted. Unsupported task options remain rejected by EngineRunner. Do not use
bare host.start/create as an alternative to this capacity authority.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from copy import deepcopy
from .bootstrap import BootstrapCoordinator
from .bootstrap_handle import BootstrapHandle
from .engine_runner import EngineRunner
from .admission import AdmissionController
from ..server.hosted_setup import build_hosted_application,HostedApplication
from ..manager import RuntimeUnavailable
from ..sidequestion import Model

@dataclass(frozen=True)
class BootstrapConfiguration:
    model:Model
    system:str
    policy_revision:str
    request_id:str
    def __post_init__(self):
        if not isinstance(self.model,Model) or not self.model.identity or not isinstance(self.system,str) or not self.system.strip():raise ValueError('Explicit identified model and system prompt required')
        if not isinstance(self.policy_revision,str) or not 1<=len(self.policy_revision)<=256 or not isinstance(self.request_id,str) or not 1<=len(self.request_id)<=128:raise ValueError('Bounded stable policy revision and bootstrap request ID required')

@dataclass(frozen=True)
class BootstrapApplication:
    hosted:HostedApplication
    admission:AdmissionController
    @property
    def app(self):return self.hosted.app
    @property
    def host(self):return self.hosted.host
    @property
    def manager(self):return self.hosted.manager
    @property
    def runner(self):return self.hosted.runner
    @property
    def facade(self):return self.hosted.facade


def build_bootstrap_application(executor,signing_key,password_codec,*,coordinator,
        bootstrap_configuration,engine_factory,initialized_readiness,initialized_factory,
        live_last_activity,capacity=1,max_pending=64,single_process=False,
        profile_available=None,application_options=None):
    if not isinstance(coordinator,BootstrapCoordinator) or coordinator.journal.executor is not executor or coordinator._executor is not executor or coordinator._journal is not coordinator.journal or coordinator.journal._executor is not executor:raise ValueError('Exact bootstrap coordinator/executor required')
    if any(not callable(f) for f in (bootstrap_configuration,engine_factory,initialized_readiness,initialized_factory,live_last_activity)):raise ValueError('Explicit pure configuration, inert factories and live activity callback required')
    if single_process is not True or type(max_pending) is not int or not 1<=max_pending<=10000 or type(capacity) is not int or not 1<=capacity<=max_pending:raise ValueError('Explicit bounded single-process capacity required')
    # Do not invoke host callbacks during construction. Preserve original resource
    # identities when they later run, including after a callback returns.
    journal=coordinator.journal
    def binding():
        if coordinator.journal is not journal or journal.executor is not executor or coordinator._executor is not executor or coordinator._journal is not journal or journal._executor is not executor:raise RuntimeUnavailable('Bootstrap setup resource changed')
    def config(task):
        binding();value=bootstrap_configuration(deepcopy(task));binding()
        if value is not None and type(value) is not BootstrapConfiguration:raise RuntimeUnavailable('Bootstrap configuration must be explicit or None')
        return value
    def readiness(task):return config(task) is not None
    def factory(task,token,manager):
        value=config(task)
        if value is None:raise RuntimeUnavailable('Bootstrap configuration changed during admission')
        return BootstrapHandle(task,token,coordinator=coordinator,manager=manager,
            model=value.model,system=value.system,policy_revision=value.policy_revision,
            request_id=value.request_id,engine_factory=engine_factory)
    runner=EngineRunner(readiness=initialized_readiness,factory=initialized_factory,max_pending=max_pending,
        initialization_readiness=readiness,bootstrap_factory=factory)
    hosted=None;controller=None
    @contextmanager
    def admission(operation,task_id):
        if hosted is None or controller is None:raise RuntimeUnavailable('Bootstrap application is not fully composed')
        if hosted.runner is not runner or controller.host is not hosted.host or hosted.manager is not hosted.host.manager or hosted.manager.runner is not runner:raise RuntimeUnavailable('Bootstrap application ownership changed')
        binding()
        with controller.admission_guard(operation,task_id) as allowed:
            yield allowed
    hosted=build_hosted_application(executor,signing_key,password_codec,runner=runner,
        admission_guard=admission,live_last_activity=live_last_activity,
        profile_available=profile_available,application_options=application_options)
    binding()
    controller=AdmissionController(hosted.host,limit=capacity,single_process=True)
    return BootstrapApplication(hosted,controller)
