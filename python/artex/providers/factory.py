"""Explicit stored-profile assembly, without credential discovery or network startup.

The injected transport_factory(Config) must be construction-only. For opt-in
HTTP, a host may pass lambda config: HTTPTransport(approved_origins). Nothing
here chooses an origin policy. Existing requests retain old providers after
invalidation; hosts must separately drain them before revoking credentials.

Retry defaults and overrides use source-pinned bounded layers (see retry.py).
Each logical profile call has an aggregate attempt/time budget. Global retry
settings are not read: callers must resolve overrides before assembly.
Proxy, rate limits and session headers are rejected. Context windows feed pool admission, not compaction.
Profile streaming=False forbids stream() rather than inventing SSE events.
prepare()/prepare_pool() construct independent providers without consulting or
publishing into the factory cache. They do not certify a database commit or grant
publication authority. The caller owns the resulting references and must arrange
any resource disposal; the transport contract provides no automatic close hook.
Existing build()/pool() caching and coordinator wiring remain unchanged.
Enrolled factories require already-held configuration ownership; preparation is
read access and explicit invalidation is write access. The legacy coordinator
refuses enrolled resources; use the opt-in committed host instead.
Coordinator mutations are serialized only within this instance; external writers
require external synchronization. Inject server.ProfileApplyPending as
pending_error to integrate ProfileService without importing server modules.
"""
from copy import deepcopy
from dataclasses import fields
from threading import RLock
import time

from . import Config, Provider
from .retry import Policy, RetryProvider, RetryTransport
from ..db.profiles import LLMProfile, ProfileInvalid, ProfileNotFound, _validate, _public_url
from ..configuration_owner import ConfigurationUnavailable
from ..db.configuration_ownership import is_bound, require_read, require_write
from ..health import Registry
from ..llmpool import Member, Pool, RANK_ACTIVE


class ApplyPending(RuntimeError):
    """Persistence completed, but the runtime has been cleared pending reload."""


class ConfiguredProvider:
    def __init__(self, provider, profile):
        self._provider = provider
        self._maximum = profile.max_tokens
        self._streaming = profile.streaming

    def _request(self, request):
        if not isinstance(request, dict):
            raise ProfileInvalid('profile provider requires a mapping request')
        request = deepcopy(request)
        maximum = request.get('max_tokens', 0)
        if type(maximum) is not int or maximum < 0:
            raise ProfileInvalid('invalid request token limit')
        if self._maximum:
            request['max_tokens'] = min(maximum, self._maximum) if maximum else self._maximum
        return request

    def complete(self, context, request):
        return self._provider.complete(context, self._request(request))

    def stream(self, context, request):
        if not self._streaming:
            raise ProfileInvalid('streaming disabled for profile; use complete')
        return self._provider.stream(context, self._request(request))


class ProfileFactory:
    def __init__(self, transport_factory, *, health=None, retry_max_attempts=64,
                 retry_max_seconds=120, clock=time.monotonic, sleep=time.sleep):
        if not callable(transport_factory):
            raise ProfileInvalid('explicit transport factory required')
        Policy(max_attempts=retry_max_attempts, max_seconds=retry_max_seconds)
        if not callable(clock) or not callable(sleep):
            raise ProfileInvalid('explicit clock and sleep must be callable')
        self._retry_max_attempts, self._retry_max_seconds = retry_max_attempts, retry_max_seconds
        self._clock, self._sleep = clock, sleep
        self._transport_factory = transport_factory
        self.health = health if health is not None else Registry()
        self._cache = {}
        self._lock = RLock()

    def validate(self, profile):
        _validate(profile)
        if profile.format not in ('openai', 'anthropic'):
            raise ProfileInvalid('provider format not supported by this runtime')
        if profile.proxy or profile.session_header_key:
            raise ProfileInvalid('proxy and session headers are not implemented')
        if profile.rate_per_second or profile.rate_per_minute:
            raise ProfileInvalid('rate limiting is not implemented')
        Policy(profile.retry, self._retry_max_attempts, self._retry_max_seconds)
        if profile.format != 'openai' and profile.max_tokens_field:
            raise ProfileInvalid('maximum-token field requires OpenAI format')
        # Enabled Anthropic thinking requires a budget/config mode not yet
        # implemented by this adapter. Reject rather than send invalid requests.
        if profile.format == 'anthropic' and profile.thinking_type == 'enabled':
            raise ProfileInvalid('Anthropic enabled thinking budget is not implemented')
        if profile.format == 'anthropic' and profile.reasoning_effort == 'xhigh':
            raise ProfileInvalid('Anthropic xhigh effort is unsupported')
        if not profile.api_key or not profile.api_key.strip():
            raise ProfileInvalid('explicit API key required')
        if any(ord(c) < 32 or ord(c) == 127 for c in profile.api_key):
            raise ProfileInvalid('invalid API key')
        config = Config(format=profile.format, model=profile.model,
                        base_url=profile.base_url, api_key=profile.api_key,
                        thinking_type=profile.thinking_type,
                        reasoning_effort=profile.reasoning_effort,
                        max_tokens_field=profile.max_tokens_field)
        # Validate URL using existing adapter without constructing transport.
        Provider(config, None)
        return config

    def build(self, profile):
        require_read(self, 'factory.build')
        profile = deepcopy(profile)
        config = self.validate(profile)
        # Private cache values include credentials, never exposed in repr/DTO.
        fingerprint = tuple(getattr(profile, f.name) for f in fields(profile))
        with self._lock:
            require_read(self, 'factory.build')
            cached = self._cache.get(profile.id)
            if cached is not None and cached[0] == fingerprint:
                require_read(self, 'factory.build')
                return cached[1]
            provider = self._construct(profile, config)
            require_read(self, 'factory.build')
            self._cache[profile.id] = (fingerprint, provider)
            return provider

    def prepare(self, profile):
        """Construct a fresh provider without reading or changing the cache.

        This performs the same validation and construction as a cache miss in
        build(), including error redaction. It invokes the supplied inert
        transport factory, never the resulting transport. It is not a commit,
        publication or revocation operation, and does not install the provider.
        """
        require_read(self, 'factory.prepare')
        profile = deepcopy(profile)
        config = self.validate(profile)
        with self._lock:
            require_read(self, 'factory.prepare')
            provider = self._construct(profile, config)
            require_read(self, 'factory.prepare')
            return provider

    def _construct(self, profile, config):
        try:
            transport = self._transport_factory(config)
        except Exception:
            raise ProfileInvalid('transport construction failed') from None
        if not callable(transport):
            raise ProfileInvalid('transport factory must return a callable')
        policy = Policy(profile.retry, self._retry_max_attempts, self._retry_max_seconds)
        transport = RetryTransport(transport, policy, clock=self._clock, sleep=self._sleep)
        retrying = RetryProvider(Provider(config, transport), policy, format=profile.format,
                                 clock=self._clock, sleep=self._sleep)
        return ConfiguredProvider(retrying, profile)

    def invalidate(self, identifier=None):
        require_write(self, 'factory.invalidate')
        with self._lock:
            require_write(self, 'factory.invalidate')
            if identifier is None:
                self._cache.clear()
            else:
                self._cache.pop(identifier, None)
        # Health has explicit persistence callbacks; callers own reset policy.

    def pool(self, profiles):
        require_read(self, 'factory.pool')
        pool = self._assemble_pool(profiles, self.build)
        require_read(self, 'factory.pool')
        return pool

    def prepare_pool(self, profiles):
        """Construct an unpublished pool with ordinary pool validation/ranking.

        Even a later member failure leaves every cache entry untouched. Earlier
        constructions may have allocated caller-owned transport resources; no
        close/revocation contract is invented here. The existing health registry
        is shared by identity and is not reset or updated during construction.
        """
        require_read(self, 'factory.prepare_pool')
        pool = self._assemble_pool(profiles, self.prepare)
        require_read(self, 'factory.prepare_pool')
        return pool

    def _assemble_pool(self, profiles, build):
        profiles = deepcopy(list(profiles))
        if any(not isinstance(p, LLMProfile) or p.id <= 0 for p in profiles):
            raise ProfileInvalid('pool requires persisted profiles')
        if len({p.id for p in profiles}) != len(profiles) or sum(p.is_default for p in profiles) > 1:
            raise ProfileInvalid('duplicate or ambiguous pool profiles')
        eligible = [p for p in profiles if p.is_default or not p.pool_exclude]
        eligible.sort(key=lambda p: (not p.is_default, -p.priority, p.id))
        members = [Member(p.id, p.name, build(p), p.model, p.format,
                          p.priority, p.is_default, RANK_ACTIVE if p.is_default else p.priority,
                          max(32, min(1000, p.context_window_k or 200)) * 1000) for p in eligible]
        return Pool(members, self.health) if members else None


def public_snapshot(profile=None):
    return dict(configured=profile is not None, provider=profile.format if profile else '',
                model=profile.model if profile else '', base_url=_public_url(profile.base_url) if profile else '',
                proxy='', key_set=bool(profile and profile.api_key), rate_per_second=0,
                rate_per_minute=0, context_window_k=profile.context_window_k if profile else 0,
                thinking_type=profile.thinking_type if profile else '',
                reasoning_effort=profile.reasoning_effort if profile else '')


class ProfileCoordinator:
    """Atomic in-process runtime publication following durable store mutations.

    runtime() returns (pool, public_snapshot). Missing active profile is allowed
    at startup; it is not substituted with a guessed active pool member.
    A failed reapplication clears publication and cache, never reports old state
    as newly applied. Existing external references cannot be forcibly revoked.
    No background watcher or automatic factory/application wiring.
    """
    def __init__(self, store, factory, *, pending_error=ApplyPending):
        if not isinstance(factory, ProfileFactory) or not all(callable(getattr(store, n, None)) for n in
            ('get', 'active', 'pool', 'save', 'set_active')):
            raise ProfileInvalid('profile store and factory prerequisites required')
        if not isinstance(pending_error, type) or not issubclass(pending_error, RuntimeError):
            raise ProfileInvalid('pending error must be a RuntimeError class')
        self._store, self._factory, self._pending_error = store, factory, pending_error
        self._lock = RLock()
        self._pool, self._snapshot = None, public_snapshot()

    def runtime(self):
        with self._lock:
            return self._pool, deepcopy(self._snapshot)

    def snapshot(self):
        return self.runtime()[1]

    def _require_unbound(self):
        # This legacy preflight/publication protocol cannot participate in an
        # enrolled domain, even when its caller holds a writer capability.
        if is_bound(self._factory) or is_bound(getattr(self._store, 'executor', None)):
            raise ConfigurationUnavailable('Legacy profile coordinator cannot use enrolled resources')

    def reload(self):
        self._require_unbound()
        with self._lock:
            self._require_unbound()
            self._pool, self._snapshot = None, public_snapshot()
            self._factory.invalidate()
            active = self._store.active()
            profiles = self._store.pool()
            if active is not None:
                self._factory.validate(active)
                matches = [p for p in profiles if p.id == active.id and p.is_default]
                if len(matches) != 1 or matches[0] != active:
                    raise ProfileInvalid('inconsistent active profile snapshot')
            elif any(p.is_default for p in profiles):
                raise ProfileInvalid('inconsistent active profile snapshot')
            pool = self._factory.pool(profiles)
            self._pool, self._snapshot = pool, public_snapshot(active)

    def _apply(self):
        self._require_unbound()
        try:
            self.reload()
        except Exception:
            self._factory.invalidate()
            self._pool, self._snapshot = None, public_snapshot()
            raise self._pending_error('profile persisted but runtime application is incomplete') from None

    def save(self, profile):
        self._require_unbound()
        with self._lock:
            self._require_unbound()
            candidate = deepcopy(profile)
            if candidate.id:
                previous = self._store.get(candidate.id)
                if previous is None:
                    raise ProfileNotFound('profile not found')
                if not candidate.api_key:
                    candidate.api_key = previous.api_key
            # Construction preflight is inert by contract and precedes writes.
            self._factory.build(candidate)
            identifier = self._store.save(profile)
            self._apply()
            return identifier

    def set_active(self, identifier):
        self._require_unbound()
        with self._lock:
            self._require_unbound()
            profile = self._store.get(identifier)
            if profile is None:
                raise ProfileNotFound('profile not found')
            self._factory.build(profile)
            self._store.set_active(identifier)
            self._apply()
