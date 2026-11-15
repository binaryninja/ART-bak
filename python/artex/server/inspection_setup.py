"""Explicit authenticated chat/profile/catalog application plus inspector routes.

This helper creates no schemas, enables no recording, registers no executable
catalog tools, and starts no listener. chat_options contains the existing
build_chat_application's mandatory host transport, readiness, authorization,
grant, tools, transcript-root and prompt policies unchanged. Optional agent
catalog wiring remains that factory's explicit choice; no builtins are seeded.

Recording scope is resolved per request, never guessed from administrator JWT
or a session substring. The host must authorize each selection and each body
and provide its own redactor. A deliberately global store is possible only via
that explicit resolver and policy. Tool metadata has a separate required policy
(request, action, path_parameters)->literal True; enabling metadata is not a
runtime grant. Policies must be pure and return False to deny, not mutate data.

All stores must share the caller-owned executor. Startup may read profiles/tasks
as documented by chat_setup, but never queries recording or tool metadata here.
Returned services are exact mounted instances for host inspection, not a safe
replacement for calling the authenticated application. Close/drain the returned
bundle before closing caller-owned database/transports. Failure during assembly
cancels and drains newly created dispatcher threads; no partial app is returned.
"""
from dataclasses import dataclass, field
from .chat_setup import build_chat_application
from .core import Router, error_response
from .llm_records import LLMRecordService, RecordUnavailable
from .tool_catalog import ToolCatalogService
from ..db.llm_records import LLMRecordStore
from ..db.tool_catalog import ToolCatalogStore


class AuthorizedToolCatalogService(ToolCatalogService):
    def __init__(self, store, authorize):
        super().__init__(store)
        self.authorize = authorize

    def handle(self, action, request, params):
        try:
            allowed = self.authorize(request, action, dict(params)) is True
        except Exception:
            return error_response(503, 'Tool catalog authorization unavailable')
        if not allowed:
            return error_response(403, 'Tool catalog access denied')
        return super().handle(action, request, params)


@dataclass(frozen=True)
class InspectionApplicationBundle:
    application: object = field(repr=False)
    runtime: object = field(repr=False)
    records: LLMRecordService = field(repr=False)
    tools: AuthorizedToolCatalogService = field(repr=False)

    def close(self, *, cancel=False, timeout=None):
        return self.runtime.close(cancel=cancel, timeout=timeout)


def build_inspection_application(executor, signing_key, password_codec, *,
                                 chat_options, record_store_resolver,
                                 record_authorize, record_redact,
                                 tool_store, tool_authorize):
    """Compose one JWT-authenticated WSGI app, with explicit inspector policies.

    record_* signatures are those of LLMRecordService; every resolved store
    must be an LLMRecordStore using executor. tool_store must be a
    ToolCatalogStore using that same executor. No default global store exists.
    chat_options is forwarded to build_chat_application without policy defaults.
    """
    if not all(callable(p) for p in (record_store_resolver, record_authorize,
                                    record_redact, tool_authorize)):
        raise ValueError('Explicit recording and tool catalog policies required')
    if not isinstance(tool_store, ToolCatalogStore) or tool_store.executor is not executor:
        raise ValueError('Tool catalog database authority must match')
    if not isinstance(chat_options, dict):
        raise ValueError('Explicit chat factory options required')

    def scoped(request, action, selection):
        store = record_store_resolver(request, action, selection)
        if not isinstance(store, LLMRecordStore) or store.executor is not executor:
            raise RecordUnavailable('Recording database authority must match')
        return store

    records = LLMRecordService(scoped, authorize=record_authorize, redact=record_redact)
    tools = AuthorizedToolCatalogService(tool_store, tool_authorize)
    # Stage routes before creating the application. Mount only after checking
    # the entire set, so accidental duplicates never yield a partial service.
    staged = Router(lambda request: False)
    records.register(staged)
    tools.register(staged)
    runtime = build_chat_application(executor, signing_key, password_codec, **dict(chat_options))
    try:
        runtime.application.router.mount_protected(staged, use_parent_authorizer=True)
        return InspectionApplicationBundle(runtime.application, runtime, records, tools)
    except BaseException:
        runtime.close(cancel=True)
        raise
