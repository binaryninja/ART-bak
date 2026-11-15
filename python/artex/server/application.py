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


def build_application(executor, signing_key: bytes, password_codec, *,
                      category_manager=None, task_manager=None, profile_available=None, asset_store=None,
                      log_ring=None, version='python-port-dev',
                      max_body_bytes=1_048_576):
    """Return a WSGI callable; executor lifecycle remains caller-owned.

    Only health/auth, category, task and log snapshot/history routes are registered. The optional manager is
    required for mutations that synchronize live task metadata; absent manager
    produces 503. This is not the full ARTEX server.
    """
    auth = AuthService(SettingsStore(executor), password_codec, signing_key)
    router = auth.router(version)
    TaskCategoryService(TaskCategoryStore(executor), category_manager).register(router)
    LogService(LogStore(executor), log_ring).register(router)
    TaskService(task_manager, profile_available=profile_available).register(router)
    AssetService(asset_store).register(router)
    return WSGIApplication(router, max_body_bytes=max_body_bytes)


def build_persisted_application(executor, signing_key: bytes, password_codec, *,
                                runner=None, profile_available=None,
                                live_last_activity=None, log_ring=None, allow_global_assets=False,
                                version='python-port-dev', max_body_bytes=1_048_576):
    """Compose actual task repositories/metrics/manager, then load without start.

    This factory reads existing task state and therefore requires the reference
    PostgreSQL schema. Read failures propagate: it never substitutes an empty
    registry for a failed database. No task is resumed during loading. Without
    an explicitly supplied cooperative runner, create/resume remain unavailable.
    Database/listener lifecycle, signing-key storage and transport are caller-owned.
    """
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise ValueError('Signing key must be at least 32 bytes')
    from ..db.tasks import TaskStore
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
                             asset_store=AssetStore(executor, allow_global=allow_global_assets),
                             version=version, max_body_bytes=max_body_bytes)
