"""Compose the implemented HTTP slice without opening connections or listeners.

Callers explicitly provide a database executor, signing key and password codec.
Use PostgresExecutor and BcryptCodec in production; never use test fakes. There
is no task/provider/tool execution or automatic migration in this factory.
"""
from ..db.settings import SettingsStore
from ..db.logs import LogStore
from ..db.task_categories import TaskCategoryStore
from .auth import AuthService
from .task_categories import TaskCategoryService
from .wsgi import WSGIApplication
from .logs import LogService
from .tasks import TaskService
from .assets import AssetService
from .findings import FindingService
from .task_templates import TaskTemplateService
from .profiles import ProfileService
from .companies import CompanyService
from .exploration import ExplorationService


def build_application(executor, signing_key: bytes, password_codec, *,
                      category_manager=None, task_manager=None, profile_available=None, asset_store=None,
                      finding_store=None, finding_asset_resolver=None, company_store=None, template_store=None,
                      profile_store=None, profile_coordinator=None, runtime_snapshot=None,
                      exploration_store_resolver=None, active_task_id=None,
                      exploration_nodeboard_factory=None, exploration_nodeboard_asset_resolver=None,
                      log_ring=None, version='python-port-dev',
                      max_body_bytes=1_048_576):
    """Return a WSGI callable; executor lifecycle remains caller-owned.

    Registers health/auth, category, task, asset, finding, company, profile and log snapshot/history
    slices. Missing stores/managers/adapters produce 503, not placeholder success.
    Finding status notifications, evidence exports and runtime-dependent routes
    remain unavailable; anchored findings require an explicit asset resolver.
    Supplied finding stores retain their own task/exploration scope unchanged.
    Exploration reads require an explicit authorized task-to-store resolver;
    optional active selection is never inferred from the task manager. Neither
    callback is invoked during construction. Nodeboard factories and asset-label
    resolvers are also explicit and receive the authorized exploration store; no
    global fallback or inherited scope is inferred. This is not the full server.
    """
    auth = AuthService(SettingsStore(executor), password_codec, signing_key)
    router = auth.router(version)
    TaskCategoryService(TaskCategoryStore(executor), category_manager).register(router)
    LogService(LogStore(executor), log_ring).register(router)
    TaskService(task_manager, profile_available=profile_available).register(router)
    AssetService(asset_store).register(router)
    FindingService(finding_store, asset_resolver=finding_asset_resolver).register(router)
    TaskTemplateService(template_store).register(router)
    CompanyService(company_store).register(router)
    ExplorationService(exploration_store_resolver, active_task_id=active_task_id,
                       nodeboard_factory=exploration_nodeboard_factory,
                       nodeboard_asset_resolver=exploration_nodeboard_asset_resolver).register(router)
    ProfileService(profile_store, coordinator=profile_coordinator,
                   runtime_snapshot=runtime_snapshot).register(router)
    return WSGIApplication(router, max_body_bytes=max_body_bytes)


def build_persisted_application(executor, signing_key: bytes, password_codec, *,
                                runner=None, profile_available=None,
                                live_last_activity=None, log_ring=None, allow_global_assets=False,
                                finding_asset_resolver=None, root_domain_resolver=None,
                                profile_coordinator=None, runtime_snapshot=None,
                                exploration_store_resolver=None, active_task_id=None,
                                exploration_nodeboard_factory=None, exploration_nodeboard_asset_resolver=None,
                                version='python-port-dev', max_body_bytes=1_048_576):
    """Compose actual task repositories/metrics/manager, then load without start.

    This factory reads existing task state and therefore requires the reference
    PostgreSQL schema. Read failures propagate: it never substitutes an empty
    registry for a failed database. No task is resumed during loading. Without
    an explicitly supplied cooperative runner, create/resume remain unavailable.
    Companies and standalone findings use authenticated administrator/global repositories;
    company construction performs no reads or external registry lookup. Company
    scope mutations/deletion remain unavailable pending full attribution cleanup;
    no inherited task scope is inferred. Finding asset labels require an explicit
    resolver; no global asset lookup is silently enabled. Asset reads remain
    scoped unless allow_global_assets is explicitly enabled. The optional offline
    root-domain resolver is forwarded, but does not bypass missing mutation guards
    or company adapters. Database/listener lifecycle, signing-key storage and
    transport are caller-owned. Exploration resolver/active selection callbacks
    are forwarded unchanged, never inferred or invoked during construction.
    """
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise ValueError('Signing key must be at least 32 bytes')
    from ..db.tasks import TaskStore
    from ..db.findings import FindingStore
    from ..db.task_templates import TaskTemplateStore
    from ..db.profiles import ProfileStore
    from ..db.companies import CompanyStore
    from ..db.asset_store import AssetStore
    from ..db.task_metrics import TaskMetricsStore, TaskMetricsEnricher
    from ..manager import TaskManager
    categories = TaskCategoryStore(executor)
    metrics = TaskMetricsEnricher(TaskMetricsStore(executor), live_last_activity=live_last_activity)
    manager = TaskManager(TaskStore(executor), categories, runner,
                          dto_enricher=metrics, profile_available=profile_available)
    manager.load_existing()
    return build_application(executor, signing_key, password_codec,
                             category_manager=manager, task_manager=manager,
                             profile_available=profile_available, log_ring=log_ring,
                             asset_store=AssetStore(executor, allow_global=allow_global_assets,
                                                    root_domain_resolver=root_domain_resolver),
                             finding_store=FindingStore(executor),
                             template_store=TaskTemplateStore(executor),
                             company_store=CompanyStore(executor),
                             profile_store=ProfileStore(executor),
                             profile_coordinator=profile_coordinator, runtime_snapshot=runtime_snapshot,
                             finding_asset_resolver=finding_asset_resolver,
                             exploration_store_resolver=exploration_store_resolver,
                             active_task_id=active_task_id,
                             exploration_nodeboard_factory=exploration_nodeboard_factory,
                             exploration_nodeboard_asset_resolver=exploration_nodeboard_asset_resolver,
                             version=version, max_body_bytes=max_body_bytes)
