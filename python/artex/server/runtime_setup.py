"""Opt-in stored-profile HTTP/runtime assembly, not a listener or agent runner.

Unlike build_application, this explicit setup reads stored active/pool profiles
and build_persisted_application loads existing tasks (without starting them).
Transport factory MUST be construction-only and enforce host-approved origins;
this module never guesses an origin allowlist, discovers keys, probes providers,
or invokes complete/stream. Health registry is mandatory and caller-owned.

The returned bundle owns its factory/coordinator references; database, transport
resources, health callbacks and application lifecycle remain host-owned. On the
original path, reload invalidates lookup caches, NOT outstanding providers. The host
must stop admission, drain outstanding calls and close its transport resources
before revoking keys/shutdown. No automatic close pretends to provide that drain.
Coordinator serializes only local mutations; external writers require external
synchronization and explicit reload. Runtime apply failure after persistence is
reported by HTTP as ProfileApplyPending, not as a successful activation.
This enables profile configuration only, not agent execution, probes, frontend
hosting or full server parity. No CLI or default assembly is modified.
"""
from dataclasses import dataclass, field
from types import MethodType
from ..db.profiles import ProfileStore
from ..db.configuration_ownership import bound_owner
from ..providers.factory import ProfileFactory, ProfileCoordinator
from ..providers.committed_profiles import CommittedProfileCoordinator
from ..providers.profile_host import ProfileHost, ProfileHostPolicy, ProfileHostResources
from ..health import Registry
from .application import build_persisted_application
from .core import Router
from .profiles import ProfileApplyPending
from .profile_reconciliation import ProfileReconciliationService
from .wsgi import WSGIApplication


_SUPPLIED_HOST_METHODS = {
    name: getattr(ProfileHost, name)
    for name in ('save', 'set_active', 'reconcile', 'snapshot', 'status', 'validate_bindings')
}


@dataclass(frozen=True)
class RuntimeBundle:
    application: object = field(repr=False)
    profile_store: ProfileStore = field(repr=False)
    profile_factory: ProfileFactory = field(repr=False)
    profile_coordinator: ProfileCoordinator | ProfileHost = field(repr=False)
    health: Registry = field(repr=False)
    profile_host: ProfileHost | None = field(default=None, repr=False)


def build_profile_runtime_application(executor, signing_key, password_codec, *,
                                      transport_factory, health, application_options=None,
                                      profile_host_policy=None, profile_reconcile_authorize=None,
                                      profile_host=None):
    """Read profiles, publish an inert runtime, and wire authenticated HTTP.

    application_options forwards existing persisted-factory knobs unchanged,
    except profile_coordinator/runtime_snapshot owned here. No default runner,
    tool, target scope, provider origin or live task snapshot is inferred.
    Failure propagates without returning a partially configured application.

    Supplying both an exact ProfileHostPolicy and an explicit request authorizer
    opts into committed mutation and protected reconciliation. Neither is
    inferred from HTTP authentication. With neither, the original path remains.
    The host owns writer participation and existing provider lifetimes; structural
    app/router checks do not establish original application issuance or authority.
    In this branch profile_coordinator/profile_host expose guarded mutations and
    observations, not the original coordinator's raw runtime provider pool.

    Alternatively, profile_host reuses an exact already-composed host on this
    executor, transport factory and health registry. Its executor and factory
    must already share one live configuration owner. This requires explicit
    reconciliation authorization and excludes profile_host_policy; it never
    constructs replacement resources, enrolls them or substitutes authority.
    Persisted application composition remains an operational boundary, not a
    proof of task lifecycle ownership or startup readiness.
    """
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise ValueError('Signing key must be at least 32 bytes')
    if not callable(transport_factory) or not isinstance(health, Registry):
        raise ValueError('Explicit construction-only transport factory and health Registry required')
    if not all(callable(getattr(password_codec, name, None)) for name in ('hash', 'verify')):
        raise ValueError('Explicit password codec required')
    if profile_host is None:
        if (profile_host_policy is None) != (profile_reconcile_authorize is None):
            raise ValueError('Profile host policy and reconciliation authorization are required together')
        if profile_host_policy is not None and (
                type(profile_host_policy) is not ProfileHostPolicy
                or not callable(profile_reconcile_authorize)):
            raise ValueError('Exact profile host policy and explicit reconciliation authorization required')
    elif (type(profile_host) is not ProfileHost or profile_host_policy is not None
          or not callable(profile_reconcile_authorize)):
        raise ValueError('Exact supplied profile host and reconciliation authorization required without another policy')
    options = dict(application_options or {})
    if {'profile_coordinator', 'runtime_snapshot'} & options.keys():
        raise ValueError('Profile runtime adapters are owned by this assembly')
    # Reject invalid factory options before profile reads/transport construction.
    import inspect
    inspect.signature(build_persisted_application).bind(executor, signing_key, password_codec,
        profile_coordinator=None, runtime_snapshot=None, **options)
    if profile_host is not None:
        return _build_supplied_host_runtime(executor, signing_key, password_codec,
            transport_factory=transport_factory, health=health, options=options,
            host=profile_host, authorize=profile_reconcile_authorize)
    if profile_host_policy is not None:
        return _build_host_runtime(executor, signing_key, password_codec,
            transport_factory=transport_factory, health=health, options=options,
            policy=profile_host_policy, authorize=profile_reconcile_authorize)
    store = ProfileStore(executor)
    factory = ProfileFactory(transport_factory, health=health)
    coordinator = ProfileCoordinator(store, factory, pending_error=ProfileApplyPending)
    coordinator.reload()
    application = build_persisted_application(executor, signing_key, password_codec,
        profile_coordinator=coordinator, runtime_snapshot=coordinator.snapshot, **options)
    return RuntimeBundle(application, store, factory, coordinator, health)


def _build_host_runtime(executor, signing_key, password_codec, *,
                        transport_factory, health, options, policy, authorize):
    """Compose only the explicit branch; never replay a failed assembly.

    Service declarations are staged before the application boundary. The new
    application is returned only after a protected atomic mount succeeds. Earlier
    profile reads/construction and caller-owned allocations are not rolled back
    or closed here when a later assembly step fails.
    """
    store = ProfileStore(executor)
    factory = ProfileFactory(transport_factory, health=health)
    coordinator = CommittedProfileCoordinator(store, factory, pending_error=ProfileApplyPending)
    host = ProfileHost(coordinator, policy=policy)
    service = ProfileReconciliationService(host, authorize=authorize)
    staged = Router(lambda _request: False)
    service.register(staged)
    host.reconcile()
    application = build_persisted_application(executor, signing_key, password_codec,
        profile_coordinator=host, runtime_snapshot=host.snapshot, **options)
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or not callable(application.router.authorize)):
        raise ValueError('Original application and protected router required')
    host.validate_bindings()
    application.router.mount_protected(staged, use_parent_authorizer=True)
    return RuntimeBundle(application, store, factory, host, health, profile_host=host)


def _supplied_host_resources(host, executor, transport_factory, health):
    """Inert association check, not a callback-provenance or authority claim."""
    for name, function in _SUPPLIED_HOST_METHODS.items():
        method = getattr(host, name, None)
        if (type(method) is not MethodType or method.__self__ is not host
                or method.__func__ is not function):
            raise ValueError('Original bound profile host methods required')
    _SUPPLIED_HOST_METHODS['validate_bindings'](host)
    association = host._policy.resources
    store, factory = host._store, host._factory
    if (type(association) is not ProfileHostResources
            or association.executor is not executor or host._executor is not executor
            or association.factory is not factory
            or factory._transport_factory is not transport_factory
            or factory.health is not health
            or host._coordinator._pending_error is not ProfileApplyPending):
        raise ValueError('Supplied host must retain the exact profile runtime resources and server pending contract')
    owner = bound_owner(executor)
    if owner is None or bound_owner(factory) is not owner:
        raise ValueError('Supplied host resources must share an existing live configuration owner')
    return store, factory


def _build_supplied_host_runtime(executor, signing_key, password_codec, *,
                                 transport_factory, health, options, host, authorize):
    """Reuse the explicit enrolled host; never repeat a failed reconciliation.

    The returned application is exposed only after original host currentness,
    resource pins and the protected mount succeed. Failure does not undo prior
    reads/construction, close caller resources or cancel admitted work.
    """
    store, factory = _supplied_host_resources(host, executor, transport_factory, health)
    service = ProfileReconciliationService(host, authorize=authorize)
    staged = Router(lambda _request: False)
    service.register(staged)
    _SUPPLIED_HOST_METHODS['reconcile'](host)
    _supplied_host_resources(host, executor, transport_factory, health)
    application = build_persisted_application(executor, signing_key, password_codec,
        profile_coordinator=host, runtime_snapshot=host.snapshot, **options)
    if (type(application) is not WSGIApplication or type(application.router) is not Router
            or not callable(application.router.authorize)):
        raise ValueError('Original application and protected router required')
    ProfileHost._current(host)
    _supplied_host_resources(host, executor, transport_factory, health)
    application.router.mount_protected(staged, use_parent_authorizer=True)
    return RuntimeBundle(application, store, factory, host, health, profile_host=host)
