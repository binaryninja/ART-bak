"""Opt-in stored-profile HTTP/runtime assembly, not a listener or agent runner.

Unlike build_application, this explicit setup reads stored active/pool profiles
and build_persisted_application loads existing tasks (without starting them).
Transport factory MUST be construction-only and enforce host-approved origins;
this module never guesses an origin allowlist, discovers keys, probes providers,
or invokes complete/stream. Health registry is mandatory and caller-owned.

The returned bundle owns its factory/coordinator references; database, transport
resources, health callbacks and application lifecycle remain host-owned. Reload
invalidates future lookup caches, NOT outstanding provider references. The host
must stop admission, drain outstanding calls and close its transport resources
before revoking keys/shutdown. No automatic close pretends to provide that drain.
Coordinator serializes only local mutations; external writers require external
synchronization and explicit reload. Runtime apply failure after persistence is
reported by HTTP as ProfileApplyPending, not as a successful activation.
This enables profile configuration only, not agent execution, probes, frontend
hosting or full server parity. No CLI or default assembly is modified.
"""
from dataclasses import dataclass, field
from ..db.profiles import ProfileStore
from ..providers.factory import ProfileFactory, ProfileCoordinator
from ..health import Registry
from .application import build_persisted_application
from .profiles import ProfileApplyPending


@dataclass(frozen=True)
class RuntimeBundle:
    application: object = field(repr=False)
    profile_store: ProfileStore = field(repr=False)
    profile_factory: ProfileFactory = field(repr=False)
    profile_coordinator: ProfileCoordinator = field(repr=False)
    health: Registry = field(repr=False)


def build_profile_runtime_application(executor, signing_key, password_codec, *,
                                      transport_factory, health, application_options=None):
    """Read profiles, publish an inert runtime, and wire authenticated HTTP.

    application_options forwards existing persisted-factory knobs unchanged,
    except profile_coordinator/runtime_snapshot owned here. No default runner,
    tool, target scope, provider origin or live task snapshot is inferred.
    Failure propagates without returning a partially configured application.
    """
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise ValueError('Signing key must be at least 32 bytes')
    if not callable(transport_factory) or not isinstance(health, Registry):
        raise ValueError('Explicit construction-only transport factory and health Registry required')
    if not all(callable(getattr(password_codec, name, None)) for name in ('hash', 'verify')):
        raise ValueError('Explicit password codec required')
    options = dict(application_options or {})
    if {'profile_coordinator', 'runtime_snapshot'} & options.keys():
        raise ValueError('Profile runtime adapters are owned by this assembly')
    # Reject invalid factory options before profile reads/transport construction.
    import inspect
    inspect.signature(build_persisted_application).bind(executor, signing_key, password_codec,
        profile_coordinator=None, runtime_snapshot=None, **options)
    store = ProfileStore(executor)
    factory = ProfileFactory(transport_factory, health=health)
    coordinator = ProfileCoordinator(store, factory, pending_error=ProfileApplyPending)
    coordinator.reload()
    application = build_persisted_application(executor, signing_key, password_codec,
        profile_coordinator=coordinator, runtime_snapshot=coordinator.snapshot, **options)
    return RuntimeBundle(application, store, factory, coordinator, health)
