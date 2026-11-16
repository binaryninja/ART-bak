"""Explicit startup-only opt-in to existing JWT task pause/resume routes.

No route, manager, admission guard or pump is rebuilt. Concurrent use during
installation is unsupported and detected conservatively; an installed owner is
never silently removed to pretend setup rollback.
"""
from dataclasses import dataclass
from .task_runtime_setup import TaskRuntimeApplication
from ..agent.task_continuation import TaskContinuationOwner
from ..agent.host import AgentHost
from ..agent.engine_runner import EngineRunner
from ..agent.admission import AdmissionController
from .task_host import TaskHTTPManagerFacade
from .tasks import TaskService
from .wsgi import WSGIApplication
from .core import Router
from .auth import AuthService
from ..db.settings import SettingsStore
from types import CodeType,FunctionType
from contextlib import contextmanager
from ..agent.bootstrap_setup import build_bootstrap_application

_HOST_INSTALL=AgentHost.install_continuation
_RUNNER_INSTALL=EngineRunner.install_continuation
_APP_CHECK=TaskRuntimeApplication._check
_HANDLES=EngineRunner.handles
_TASK_HANDLER=next(c for c in TaskService.register.__code__.co_consts if isinstance(c,CodeType) and c.co_name=='<lambda>')
_FACADE_CONTROL=TaskHTTPManagerFacade.control_task
_ADMISSION_CODE=next(c for c in build_bootstrap_application.__code__.co_consts if isinstance(c,CodeType) and c.co_name=='admission')
_GUARD_CODE=contextmanager(lambda:None).__code__
_AUTHORIZED=AuthService.authorized
_FACADE_METHODS={n:getattr(TaskHTTPManagerFacade,n) for n in ('_identity','_admit','control_task')}
_ADMISSION_METHODS={n:getattr(AdmissionController,n) for n in ('_identity','_reserve','_poll_owned','admission_guard')}
_SERVICE_METHODS={n:getattr(TaskService,n) for n in ('_handle','_dispatch')}
_AUTH_HANDLER=next(c for c in AuthService.router.__code__.co_consts if isinstance(c,CodeType) and 'self' in c.co_freevars)
@dataclass(frozen=True)
class TaskContinuationBundle:
    application:TaskRuntimeApplication
    owner:TaskContinuationOwner

def _unused(application):
    from ..agent.pump import EnginePump
    if TaskRuntimeApplication._check is not _APP_CHECK or getattr(application._check,'__func__',None) is not _APP_CHECK or type(application.pump) is not EnginePump:raise ValueError('Original composed runtime required')
    _APP_CHECK(application)
    h=application.host;m=application.manager;r=application.runner
    facade=application.facade;admission=application.admission;base=application.bootstrap.app
    if (type(facade) is not TaskHTTPManagerFacade or facade.host is not h or facade._manager is not m
        or getattr(facade.control_task,'__func__',None) is not _FACADE_CONTROL
        or type(admission) is not AdmissionController or admission.host is not h
        or admission._manager is not m or admission._runner is not r
        or type(base) is not WSGIApplication or type(base.router) is not Router):raise ValueError('Original HTTP lifecycle composition required')
    for obj,methods in ((facade,_FACADE_METHODS),(admission,_ADMISSION_METHODS)):
        if any(getattr(type(obj),name) is not method or getattr(getattr(obj,name),'__func__',None) is not method for name,method in methods.items()):raise ValueError('Original lifecycle methods required')
    authorization=base.router.authorize;auth=getattr(authorization,'__self__',None)
    if (type(auth) is not AuthService or AuthService.authorized is not _AUTHORIZED
        or getattr(authorization,'__func__',None) is not _AUTHORIZED
        or type(auth.store) is not SettingsStore or auth.store.executor is not application.runtime.executor
        or type(auth.key) is not bytes or len(auth.key)<32 or not callable(auth.clock)):raise ValueError('Original JWT owner required')
    login=[row for row in base.router._routes if row[0]=='POST' and row[1]=='/api/auth/login']
    if len(login)!=1:raise ValueError('Original auth routes required')
    login_handler=login[0][3]
    if (getattr(login_handler,'__code__',None) is not _AUTH_HANDLER or login_handler.__closure__ is None
        or len(login_handler.__closure__)!=1 or login_handler.__closure__[0].cell_contents is not auth):raise ValueError('Original bound JWT application required')
    guard=facade._admission;wrapped=getattr(guard,'__wrapped__',None)
    if (type(guard) is not FunctionType or guard.__code__ is not _GUARD_CODE
        or type(wrapped) is not FunctionType or wrapped.__code__ is not _ADMISSION_CODE
        or guard.__closure__ is None or len(guard.__closure__)!=1 or guard.__closure__[0].cell_contents is not wrapped):raise ValueError('Original shared capacity guard required')
    captured=dict(zip(wrapped.__code__.co_freevars,(cell.cell_contents for cell in wrapped.__closure__)))
    if captured.get('controller') is not admission or captured.get('hosted') is not application.bootstrap.hosted or captured.get('runner') is not r:raise ValueError('Original shared admission closure required')
    routes=[row for row in base.router._routes if row[0]=='POST' and row[1]=='/api/tasks/{id}/control']
    if len(routes)!=1:raise ValueError('Original task control route required')
    handler=routes[0][3]
    if (getattr(handler,'__code__',None) is not _TASK_HANDLER or handler.__defaults__!=('control',)
        or handler.__closure__ is None or len(handler.__closure__)!=1 or routes[0][4] is not False):raise ValueError('Original protected control handler required')
    service=handler.__closure__[0].cell_contents
    if type(service) is not TaskService or service.manager is not facade:raise ValueError('Original task service facade required')
    if any(getattr(type(service),name) is not method or getattr(getattr(service,name),'__func__',None) is not method for name,method in _SERVICE_METHODS.items()):raise ValueError('Original task handler methods required')
    if type(h) is not AgentHost or type(r) is not EngineRunner:raise ValueError('Original host and runner required')
    with h._condition:
        if h._working or h._settling or h._draining or h._mutations or h._intent_control is not None:raise ValueError('Continuation setup requires inactive host')
    with m._lock:
        if m._runs or m._suspensions or m._deadline_slots or m._unstamped_slots:raise ValueError('Continuation setup requires unused execution registry')
    if EngineRunner.handles is not _HANDLES or getattr(r.handles,'__func__',None) is not _HANDLES:raise ValueError('Original runner inventory required')
    if _HANDLES(r) or application.pump._thread is not None:raise ValueError('Continuation setup requires stopped unused pump')

def enable_task_continuation(application,*,authorize):
    if type(application) is not TaskRuntimeApplication or not callable(authorize):raise ValueError('Exact runtime application and explicit continuation grant required')
    if getattr(application.host.install_continuation,'__func__',None) is not _HOST_INSTALL or AgentHost.install_continuation is not _HOST_INSTALL or getattr(application.runner.install_continuation,'__func__',None) is not _RUNNER_INSTALL or EngineRunner.install_continuation is not _RUNNER_INSTALL:raise ValueError('Original continuation installer required')
    _unused(application)
    if application.manager._continuation_owner is not None or application.runner._continuation_owner is not None:raise ValueError('Continuation is already configured')
    owner=TaskContinuationOwner(application,authorize=authorize);owner.validate_bindings()
    if _HOST_INSTALL(application.host,owner) is not True:raise RuntimeError('Continuation installation was not confirmed')
    owner.validate_bindings();_unused(application)
    if application.manager._continuation_owner is not owner or application.runner._continuation_owner is not owner:raise RuntimeError('Continuation installation changed; ownership retained')
    return TaskContinuationBundle(application,owner)
