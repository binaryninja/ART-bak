"""Compose the implemented HTTP slice without opening connections or listeners.

Callers explicitly provide a database executor, signing key and password codec.
Use PostgresExecutor and BcryptCodec in production; never use test fakes. There
is no task/provider/tool execution or automatic migration in this factory.
"""
from ..db.settings import SettingsStore
from ..db.task_categories import TaskCategoryStore
from .auth import AuthService
from .task_categories import TaskCategoryService
from .wsgi import WSGIApplication


def build_application(executor, signing_key: bytes, password_codec, *,
                      category_manager=None, version='python-port-dev',
                      max_body_bytes=1_048_576):
    """Return a WSGI callable; executor lifecycle remains caller-owned.

    Only health/auth and category routes are registered. The optional manager is
    required for mutations that synchronize live task metadata; absent manager
    produces 503. This is not the full ARTEX server.
    """
    auth = AuthService(SettingsStore(executor), password_codec, signing_key)
    router = auth.router(version)
    TaskCategoryService(TaskCategoryStore(executor), category_manager).register(router)
    return WSGIApplication(router, max_body_bytes=max_body_bytes)
