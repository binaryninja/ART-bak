"""Compose the implemented HTTP slice without opening connections or listeners.

Callers explicitly provide a database executor, signing key and password codec.
Use PostgresExecutor and BcryptCodec in production; never use test fakes. There
is no task/provider/tool execution or automatic migration in this factory.
"""
from .task_activity import TaskActivityService
from ..db.task_activity_read import TaskActivityReadStore
from .main_sessions import MainSessionsService
from ..db.main_sessions import MainSessionsStore
from .task_intent_assets import TaskIntentAssetsService
from ..db.task_intent_assets import TaskIntentAssetsStore
from ..db.task_coverage import TaskCoverageStore
from .intercept_inspection import InterceptInspectionService
from ..db.intercept_history import InterceptHistoryStore
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
from .conversations import ConversationService
from .profiles import ProfileService
from .companies import CompanyService
from .stats import StatsService
from .sidequestions import SideQuestionHTTPService
from .exploration import ExplorationService
from .task_coverage import TaskCoverageService
from .traffic_read import TrafficReadService
from .workspace_mutations import WorkspaceMutationService
from .workspace_upload import WorkspaceUploadService
from .audit import AuditService
from .visibility import VisibilityService
from ..db.visibility import VisibilityStore
from .intercept_rules import InterceptRuleService
from ..db.intercept_rules import InterceptRuleStore
from .token_usage import TokenUsageService
from ..db.token_usage import TokenUsageStore
from .command_history import CommandHistoryService
from ..db.command_history import CommandHistoryStore
from .pool_health import PoolHealthService
from ..health import Registry


def _validate_coverage_service(executor, service):
    if service is not None and (type(service) is not TaskCoverageService or service._executor is not executor):
        raise ValueError("Coverage service requires the exact application executor")


def _validate_traffic_service(service):
    if service is not None and type(service) is not TrafficReadService:
        raise ValueError("Explicit fixed-workspace traffic service required")


def _validate_intercept_service(executor, service):
    if service is None:return
    if (type(service) is not InterceptRuleService or type(service.store) is not InterceptRuleStore
        or service.store is not service._store or service._executor is not executor
        or service.store.executor is not executor or service.store._executor is not executor
        or service.store.scope is not service._scope or service.store._scope is not service._scope
        or service.store.guard is not service._guard):
        raise ValueError('Intercept service requires exact application executor and fixed scope')


def _validate_workspace_mutation_service(service):
    if service is not None and type(service) is not WorkspaceMutationService:
        raise ValueError("Explicit scoped workspace mutation service required")


def _validate_token_usage_service(executor, service):
    if service is None:return
    if (type(service) is not TokenUsageService or type(service.store) is not TokenUsageStore
        or service.store is not service._store or service._executor is not executor
        or service.store.executor is not executor or service.store._executor is not executor
        or service.store.scope is not service._scope or service.store._scope is not service._scope
        or service.store.guard is not service._guard):
        raise ValueError('Token usage requires the exact application executor and fixed scope')


def _validate_workspace_upload_service(service):
    if service is not None and type(service) is not WorkspaceUploadService:
        raise ValueError("Explicit scoped workspace upload service required")


def _validate_task_activity_service(executor, service):
    if service is None:return
    if type(service) is not TaskActivityService or type(service.store) is not TaskActivityReadStore:
        raise ValueError('Explicit fixed task activity service required')
    store=service.store
    s,e,scope,guard,authorize,current,wait,batches,polls=service._fixed
    se,ss,sg,maximum,rows=store._fixed
    if (store is not s or e is not executor or se is not executor or store.executor is not executor
        or store.scope is not scope or ss is not scope or store.guard is not guard or sg is not guard
        or service.authorize is not authorize or service.stream_current is not current or service.wait is not wait
        or (service.max_batches,service.max_polls)!=(batches,polls)
        or (store.max_bytes,store.max_rows)!=(maximum,rows)
        or any(a is not b for a,b in zip(store.pins(),store._initial_policies))):
        raise ValueError('Task activity requires original scope, policies and application executor')


def _validate_command_history_service(executor, service):
    if service is None:return
    if (type(service) is not CommandHistoryService or type(service.store) is not CommandHistoryStore
        or service.store is not service._store or service._executor is not executor
        or service.store.executor is not executor or service.store._executor is not executor
        or service.store.scope is not service._scope or service.store._scope is not service._scope
        or service.store.guard is not service._guard):
        raise ValueError('Command history requires exact application executor and fixed scope')


def _validate_visibility_service(executor, service):
    if service is None:
        return
    if type(service) is not VisibilityService or type(service.store) is not VisibilityStore:
        raise ValueError("Explicit scoped visibility service required")
    store = service.store
    if (store is not service._store or service._executor is not executor
            or store.executor is not executor or store._executor is not executor
            or store.scope is not service._scope or store._scope is not service._scope
            or store.guard is not service._guard or store._guard is not service._guard):
        raise ValueError("Visibility service requires original store, scope, guard and application executor")


def _validate_main_sessions_service(executor, service):
    if service is None:return
    if (type(service) is not MainSessionsService or type(service.store) is not MainSessionsStore
        or service.store is not service._store or service._executor is not executor
        or service.store.executor is not executor or service.store._executor is not executor
        or service.store.scope is not service._scope or service.store._scope is not service._scope
        or service.store.guard is not service._guard or service.store._guard is not service._guard
        or not callable(service.authorize) or not callable(service.store.authorize)
        or not callable(service.store.current)):
        raise ValueError('Main sessions require original resources and application executor')


def _validate_intent_assets_service(executor, service):
    if service is None:return
    if type(service) is not TaskIntentAssetsService or type(service.store) is not TaskIntentAssetsStore:
        raise ValueError('Explicit intent asset service required')
    store=service.store;c=store.coverage
    if (type(c) is not TaskCoverageStore or store is not service._store or c is not store._coverage
        or service._executor is not executor or store.executor is not executor
        or c.executor is not executor or c._executor is not executor
        or store.scope is not service._scope or c.scope is not service._scope or c._scope is not service._scope
        or store.guard is not service._guard or c.guard is not service._guard
        or len(store._pins)!=5
        or any(a is not b for a,b in zip(store._pins,(c.executor,c.scope,c.guard,c.authorize,c.current)))
        or not callable(service.authorize)):
        raise ValueError('Intent asset service requires original fixed resources and application executor')


def _validate_intercept_inspection_service(executor, service):
    if service is None:return
    if (type(service) is not InterceptInspectionService or type(service.store) is not InterceptHistoryStore
        or service.store is not service._store or service._executor is not executor
        or service.store.executor is not executor or service.store._executor is not executor
        or service.store.scope is not service._scope or service.store._scope is not service._scope
        or service.store.guard is not service._guard or service.store._guard is not service._guard):
        raise ValueError('Intercept inspection requires original resources and application executor')


def _validate_pool_health_service(executor, service):
    if service is None:return
    if (type(service) is not PoolHealthService or type(service.settings) is not SettingsStore
        or service.settings is not service._settings or service._executor is not executor
        or service.settings.executor is not executor or type(service.registry) is not Registry
        or service.registry is not service._registry or service.guard is not service._guard
        or service.scope!=service._scope):
        raise ValueError('Pool service requires exact application settings and registry bindings')


def _validate_audit_service(service):
    if service is None:return
    if type(service) is not AuditService:
        raise ValueError('Explicit task-local audit service required')
    # Identity-only validation. Do not resolve a task or invoke a policy here.
    values=(service.resolve,service.authorize_selection,service.raw_authorize,service.current,service.guard)
    if (len(service._callbacks)!=5
            or any(a is not b for a,b in zip(values,service._callbacks))):
        raise ValueError('Audit service requires original callback bindings')


def build_application(executor, signing_key: bytes, password_codec, *,
                      category_manager=None, task_manager=None, profile_available=None, asset_store=None,
                      finding_store=None, finding_asset_resolver=None, company_store=None,
                      stats_store=None, stats_runtime_snapshot=None, template_store=None,
                      conversation_store=None, conversation_running=None,
                      conversation_agent_available=None, conversation_profile_available=None,
                      profile_store=None, profile_coordinator=None, runtime_snapshot=None,
                      exploration_store_resolver=None, active_task_id=None,
                      exploration_nodeboard_factory=None, exploration_nodeboard_asset_resolver=None,
                      side_parent_resolver=None, side_request_resolver=None, side_model_available=None,
                      side_coordinator=None, side_dispatcher=None, side_prepare_question=None,
                      side_cancel_request=None, side_clear_history=None, side_wait=None,
                      log_ring=None, coverage_service=None, traffic_service=None, intercept_service=None, token_usage_service=None, command_history_service=None, task_activity_service=None, pool_health_service=None, intercept_inspection_service=None, intent_assets_service=None, main_sessions_service=None, audit_service=None, workspace_mutation_service=None, workspace_upload_service=None, visibility_service=None, version='python-port-dev',
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
    global fallback or inherited scope is inferred. Side-question resolvers,
    coordinator, dispatcher, preparation and lifecycle adapters are explicit;
    construction never starts dispatch workers or performs provider calls. Conversation
    metadata uses explicit agent/profile/running resolvers, never inferred idle state
    or provider readiness. Conversation store construction performs no reads.
    Optional coverage_service installs only the three source read routes. It must
    use this exact executor and retain explicit task/source/connector policy. No
    coverage service is created by default; archived contexts remain unsupported.
    Optional traffic_service is an already constructed fixed-workspace SQLite
    service with explicit metadata/raw-data grants. It is independent of the
    PostgreSQL executor: no task ownership, store construction, capture or
    default traffic registration is inferred. Blob reads remain unavailable.
    Optional intercept_service is a pre-authorized fixed task OR global metadata
    store using this exact executor; no grants, idle state or enforcement/cache
    updates are inferred. Frontends must send X-Rule-Updated-At with the exact
    ISO timestamp from the last read for update/toggle/delete. No routes are
    registered by default; this is not a live policy engine integration.
    Optional workspace_mutation_service is independent of PostgreSQL and retains
    its explicit root/writer guard/action policies, including separate permanent
    deletion authority. Only mkdir with existing parents and file/empty-directory
    deletion are supported; no recursive semantics, root inference or defaults.
    Optional token_usage_service retains its fixed global OR exact task scope.
    A global service supports daily/conversations, while a task service requires
    explicit matching ?task= for exploration totals; neither infers global/active
    task grants. Missing/archive data is not fabricated zero usage, and no prices
    or costs are inferred. No token routes are registered by default.
    Optional workspace_upload_service retains explicit single-file multipart
    policies; no folder creation/extraction or root inference. Raw request bytes
    pass unchanged after the existing global body limit. That limit is never
    expanded for uploads: the smaller global/service bound wins.
    Optional task_activity_service exposes bounded local history/detail and finite
    SSE replay for ONE explicitly scoped task. No inherited/archive reads, ambient
    task selection, broadcaster or completion-at-EOF claim; no default registration.
    Original resource and policy identities are checked before startup reads.

    Optional command_history_service exposes historical tool inputs/results (not
    merely shell commands), never command execution. The legacy ?task selector
    means exploration ID. Its exact scoped/global grants and separate raw-data
    policy remain explicit; no default routes or inferred narrowing/global reads.
    Optional visibility_service retains exact scoped store/executor/guard and
    independent read/mutation policies. Capability-selection metadata does not
    authorize execution; no seeding, runtime catalog/cache activation or reload
    is inferred. Absent service installs no visibility routes.
    Optional audit_service exposes only explicitly resolved task Guard diagnostics;
    it is independent of PostgreSQL, and never infers a task or global log view.
    Absent service installs no audit route. Construction invokes no audit callback.
    Optional main_sessions_service reads only its fixed task main-session metadata;
    explicit task query/read grants required, no session creation or active fallback.

    Optional intent_assets_service exposes only its fixed task intent targets with
    explicit per-source read grants; no discovery/testing or implicit global scope.

    Optional intercept_inspection_service reads persisted pending requests and explicit
    history pages under fixed text task/workspace scope. No raw grants, decisions,
    live-waiter authority, or unpaged history behavior are inferred.

    Optional pool_health_service uses an explicitly supplied Registry and this
    executor's SettingsStore. No profiles/providers, read grants, reset approval,
    idle policy or cache coordination are inferred. Reset-all requires an approved bounded complete registry inventory; raw
    diagnostic errors remain redacted.
    No pool/retry routes are registered by default.
    This is not the full server.
    """
    _validate_coverage_service(executor, coverage_service)
    _validate_traffic_service(traffic_service)
    _validate_workspace_mutation_service(workspace_mutation_service)
    _validate_workspace_upload_service(workspace_upload_service)
    _validate_visibility_service(executor, visibility_service)
    _validate_intercept_service(executor, intercept_service)
    _validate_token_usage_service(executor, token_usage_service)
    _validate_command_history_service(executor, command_history_service)
    _validate_task_activity_service(executor, task_activity_service)
    _validate_pool_health_service(executor, pool_health_service)
    _validate_intercept_inspection_service(executor, intercept_inspection_service)
    _validate_intent_assets_service(executor, intent_assets_service)
    _validate_main_sessions_service(executor, main_sessions_service)
    _validate_audit_service(audit_service)
    auth = AuthService(SettingsStore(executor), password_codec, signing_key)
    router = auth.router(version)
    TaskCategoryService(TaskCategoryStore(executor), category_manager).register(router)
    LogService(LogStore(executor), log_ring).register(router)
    TaskService(task_manager, profile_available=profile_available).register(router)
    AssetService(asset_store).register(router)
    FindingService(finding_store, asset_resolver=finding_asset_resolver).register(router)
    TaskTemplateService(template_store).register(router)
    ConversationService(conversation_store, running=conversation_running,
                        agent_available=conversation_agent_available,
                        profile_available=conversation_profile_available).register(router)
    CompanyService(company_store).register(router)
    StatsService(stats_store, runtime_snapshot=stats_runtime_snapshot).register(router)
    ExplorationService(exploration_store_resolver, active_task_id=active_task_id,
                       nodeboard_factory=exploration_nodeboard_factory,
                       nodeboard_asset_resolver=exploration_nodeboard_asset_resolver).register(router)
    ProfileService(profile_store, coordinator=profile_coordinator,
                   runtime_snapshot=runtime_snapshot).register(router)
    SideQuestionHTTPService(side_parent_resolver, request_resolver=side_request_resolver,
                            model_available=side_model_available, coordinator=side_coordinator,
                            dispatcher=side_dispatcher, prepare_question=side_prepare_question,
                            cancel_request=side_cancel_request, clear_history=side_clear_history,
                            wait=side_wait).register(router)
    if coverage_service is not None:
        coverage_service.register(router)
    if traffic_service is not None:
        traffic_service.register(router)
    if intercept_service is not None:
        intercept_service.register(router)
    if workspace_mutation_service is not None:
        workspace_mutation_service.register(router)
    if token_usage_service is not None:
        token_usage_service.register(router)
    if workspace_upload_service is not None:
        workspace_upload_service.register(router)
    if command_history_service is not None:
        command_history_service.register(router)
    if task_activity_service is not None:
        task_activity_service.register(router)
    if visibility_service is not None:
        visibility_service.register(router)
    if main_sessions_service is not None:
        main_sessions_service.register(router)
    if intent_assets_service is not None:
        intent_assets_service.register(router)
    if intercept_inspection_service is not None:
        intercept_inspection_service.register(router)
    if pool_health_service is not None:
        pool_health_service.register(router)
    if audit_service is not None:
        audit_service.register(router)
    return WSGIApplication(router, max_body_bytes=max_body_bytes)


def build_persisted_application(executor, signing_key: bytes, password_codec, *,
                                runner=None, profile_available=None,
                                conversation_running=None, conversation_agent_available=None,
                                conversation_profile_available=None, conversation_read_limits=None,
                                live_last_activity=None, log_ring=None, allow_global_assets=False,
                                finding_asset_resolver=None, root_domain_resolver=None,
                                profile_coordinator=None, runtime_snapshot=None, stats_runtime_snapshot=None,
                                exploration_store_resolver=None, active_task_id=None,
                                exploration_nodeboard_factory=None, exploration_nodeboard_asset_resolver=None,
                                side_parent_resolver=None, side_request_resolver=None, side_model_available=None,
                                side_coordinator=None, side_dispatcher=None, side_prepare_question=None,
                                side_cancel_request=None, side_clear_history=None, side_wait=None,
                                coverage_service=None, traffic_service=None, intercept_service=None, token_usage_service=None, command_history_service=None, task_activity_service=None, pool_health_service=None, intercept_inspection_service=None, intent_assets_service=None, main_sessions_service=None, audit_service=None, workspace_mutation_service=None, workspace_upload_service=None, visibility_service=None, version='python-port-dev', max_body_bytes=1_048_576):
    """Compose actual task repositories/metrics/manager, then load without start.

    This factory reads existing task state and therefore requires the reference
    PostgreSQL schema. Read failures propagate: it never substitutes an empty
    registry for a failed database. No task is resumed during loading. Without
    an explicitly supplied cooperative runner, create/resume remain unavailable.
    Companies and standalone findings use authenticated administrator/global repositories;
    Dashboard counts likewise use deliberate administrator global access, but
    still require an explicit live stats snapshot (never inferred from storage).
    No startup aggregate reads occur. Company construction performs no reads or
    external registry lookup. Company
    scope mutations/deletion remain unavailable pending full attribution cleanup;
    no inherited task scope is inferred. Finding asset labels require an explicit
    resolver; no global asset lookup is silently enabled. Asset reads remain
    scoped unless allow_global_assets is explicitly enabled. The optional offline
    root-domain resolver is forwarded, but does not bypass missing mutation guards
    or company adapters. Database/listener lifecycle, signing-key storage and
    transport are caller-owned. Exploration resolver/active selection callbacks
    are forwarded unchanged, never inferred or invoked during construction.
    """
    from ..db.conversations import ConversationReadLimits
    if conversation_read_limits is None:conversation_read_limits=ConversationReadLimits()
    if type(conversation_read_limits)is not ConversationReadLimits:raise ValueError('Explicit conversation read limits required')
    conversation_read_limits.validate()
    _validate_coverage_service(executor, coverage_service)
    _validate_traffic_service(traffic_service)
    _validate_workspace_mutation_service(workspace_mutation_service)
    _validate_workspace_upload_service(workspace_upload_service)
    _validate_visibility_service(executor, visibility_service)
    _validate_intercept_service(executor, intercept_service)
    _validate_token_usage_service(executor, token_usage_service)
    _validate_command_history_service(executor, command_history_service)
    _validate_task_activity_service(executor, task_activity_service)
    _validate_pool_health_service(executor, pool_health_service)
    _validate_intercept_inspection_service(executor, intercept_inspection_service)
    _validate_intent_assets_service(executor, intent_assets_service)
    _validate_main_sessions_service(executor, main_sessions_service)
    _validate_audit_service(audit_service)
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise ValueError('Signing key must be at least 32 bytes')
    from ..db.tasks import TaskStore
    from ..db.findings import FindingStore
    from ..db.task_templates import TaskTemplateStore
    from ..db.conversations import ConversationStore
    from ..db.profiles import ProfileStore
    from ..db.companies import CompanyStore
    from ..db.stats import StatsStore
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
                             conversation_store=ConversationStore(executor,read_limits=conversation_read_limits),
                             conversation_running=conversation_running,
                             conversation_agent_available=conversation_agent_available,
                             conversation_profile_available=conversation_profile_available,
                             company_store=CompanyStore(executor),
                             stats_store=StatsStore(executor, allow_global=True),
                             stats_runtime_snapshot=stats_runtime_snapshot,
                             profile_store=ProfileStore(executor),
                             profile_coordinator=profile_coordinator, runtime_snapshot=runtime_snapshot,
                             finding_asset_resolver=finding_asset_resolver,
                             exploration_store_resolver=exploration_store_resolver,
                             active_task_id=active_task_id,
                             exploration_nodeboard_factory=exploration_nodeboard_factory,
                             exploration_nodeboard_asset_resolver=exploration_nodeboard_asset_resolver,
                             side_parent_resolver=side_parent_resolver,
                             side_request_resolver=side_request_resolver,
                             side_model_available=side_model_available, side_coordinator=side_coordinator,
                             side_dispatcher=side_dispatcher, side_prepare_question=side_prepare_question,
                             side_cancel_request=side_cancel_request, side_clear_history=side_clear_history,
                             side_wait=side_wait,
                             coverage_service=coverage_service, traffic_service=traffic_service,
                             workspace_mutation_service=workspace_mutation_service,
                             workspace_upload_service=workspace_upload_service, visibility_service=visibility_service, intercept_service=intercept_service, token_usage_service=token_usage_service, command_history_service=command_history_service, task_activity_service=task_activity_service, pool_health_service=pool_health_service, intercept_inspection_service=intercept_inspection_service, intent_assets_service=intent_assets_service, main_sessions_service=main_sessions_service, audit_service=audit_service,
                             version=version, max_body_bytes=max_body_bytes)
