"""Explicit EngineRunner/AgentHost/HTTP composition, without execution startup.

The caller owns executor and runner lifetimes and must supply a shared-capacity
admission_guard coordinated with queued admissions. No readiness predicate is
substituted for capacity reservation. An already-owned runner with retained
handles is rejected rather than silently adopted by a new manager. All pure
configuration and HTTP composition validate before load_existing reads storage.
Existing tasks are loaded but never resumed; no bootstrap, graph creation,
provider calls, threads, advancement, polling or completion occur at construction.

Task mutations go through TaskHTTPManagerFacade; categories use the same actual
manager for their metadata-only paths. Components are exposed for explicit host
lifecycle ownership. No close method pretends to cancel/drain external work:
use host.pause/host.drain and retain unresolved handles before releasing storage.
No task-chat, side-question, queue scheduler or optional domain adapters are
inferred. Existing application factories remain unchanged.
"""
from dataclasses import dataclass
import inspect
from ..agent.engine_runner import EngineRunner
from ..agent.host import AgentHost
from ..db.tasks import TaskStore
from ..db.task_categories import TaskCategoryStore
from ..db.task_metrics import TaskMetricsStore,TaskMetricsEnricher
from ..manager import TaskManager,RuntimeUnavailable
from .task_host import TaskHTTPManagerFacade
from .application import build_application


@dataclass(frozen=True)
class HostedApplication:
    app:object
    manager:TaskManager
    host:AgentHost
    runner:EngineRunner
    facade:TaskHTTPManagerFacade


def build_hosted_application(executor,signing_key,password_codec,*,runner,admission_guard,
                             live_last_activity,profile_available=None,application_options=None):
    if not isinstance(runner,EngineRunner):raise RuntimeUnavailable('Explicit EngineRunner required')
    if not callable(admission_guard) or not callable(live_last_activity):
        raise ValueError('Explicit shared-capacity guard and live activity callback required')
    if profile_available is not None and not callable(profile_available):raise ValueError('Invalid profile availability callback')
    if type(signing_key) is not bytes or len(signing_key)<32:raise ValueError('At least 32 signing-key bytes required')
    if any(not callable(getattr(password_codec,n,None)) for n in ('hash','verify')):raise ValueError('Explicit password codec required')
    if any(not callable(getattr(executor,n,None)) for n in ('fetch_one','fetch_all','execute','transaction')):
        raise ValueError('Explicit transactional executor required')
    if application_options is not None and type(application_options) is not dict:raise ValueError('Application options must be a mapping')
    options=dict(application_options or {})
    reserved={'executor','signing_key','password_codec','category_manager','task_manager','profile_available'}
    allowed=set(inspect.signature(build_application).parameters)-reserved
    if set(options)-allowed:raise ValueError('Unknown or protected application option')
    if 'max_body_bytes' in options and (type(options['max_body_bytes']) is not int or options['max_body_bytes']<=0):
        raise ValueError('Positive HTTP body bound required')
    # Constructors below perform no SQL or provider work. Build every HTTP
    # dependency before the sole startup read, preserving failed-config ordering.
    if runner.handles():raise RuntimeUnavailable('Runner already owns work; cannot attach a second manager')
    categories=TaskCategoryStore(executor)
    metrics=TaskMetricsEnricher(TaskMetricsStore(executor),live_last_activity=live_last_activity)
    manager=TaskManager(TaskStore(executor),categories,runner,dto_enricher=metrics,profile_available=profile_available)
    host=AgentHost(manager,runner)
    facade=TaskHTTPManagerFacade(host,admission_guard=admission_guard)
    app=build_application(executor,signing_key,password_codec,category_manager=manager,
                          task_manager=facade,profile_available=profile_available,**options)
    manager.load_existing()
    return HostedApplication(app,manager,host,runner,facade)
