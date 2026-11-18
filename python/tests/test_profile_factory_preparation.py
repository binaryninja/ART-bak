"""Construction-only preparation evidence; no transport/model/DB execution."""
from copy import deepcopy
import unittest

from artex.db.profiles import LLMProfile, ProfileInvalid, RetryOverride, RetryRule
from artex.health import Registry, State
from artex.llmpool import RANK_ACTIVE
from artex.providers import ProtocolError
from artex.providers.factory import ConfiguredProvider, ProfileFactory


def profile(**changes):
    values = dict(id=1, name='Synthetic', model='model', api_key='synthetic-not-a-credential',
                  retry=RetryOverride(*[RetryRule(-1, 0)] * 3))
    values.update(changes)
    return LLMProfile(**values)


class AbortConstruction(BaseException): pass


class InertConstruction:
    def __init__(self):
        self.configs = []
        self.callback = None
        self.effect_calls = []

    def forbidden(self, *args, **kwargs):
        self.effect_calls.append('forbidden runtime effect')
        raise AssertionError('No transport, clock, sleep or persistence invocation permitted')

    def __call__(self, config):
        self.configs.append(config)
        if self.callback is not None:
            self.callback(config)
        return self.forbidden


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.construction = InertConstruction()
        self.health = Registry(persist=self.construction.forbidden,
                               forget=self.construction.forbidden,
                               clock=self.construction.forbidden)
        self.health.restore(9, State(fails=2, trips=1, last_error='synthetic'))
        self.factory = ProfileFactory(self.construction, health=self.health,
            retry_max_attempts=7, retry_max_seconds=12,
            clock=self.construction.forbidden, sleep=self.construction.forbidden)
        self.addCleanup(self.assertEqual, self.construction.effect_calls, [])

    def cache_snapshot(self):
        return (self.factory._cache, dict(self.factory._cache),
                self.health.inventory(100), self.health.snapshot())

    def assert_preserved(self, snapshot):
        cache, entries, inventory, state = snapshot
        self.assertIs(self.factory._cache, cache)
        self.assertEqual(self.factory._cache, entries)
        for key, value in entries.items():
            self.assertIs(self.factory._cache[key], value)
        self.assertEqual(self.health.inventory(100), inventory)
        self.assertEqual(self.health.snapshot(), state)

    def seed(self):
        first = self.factory.build(profile())
        other = self.factory.build(profile(id=9, name='Other', model='other'))
        return first, other

    def test_each_prepare_is_fresh_and_never_populates_empty_cache(self):
        before = self.cache_snapshot()
        first = self.factory.prepare(profile())
        second = self.factory.prepare(profile())
        self.assertIsInstance(first, ConfiguredProvider)
        self.assertIsNot(first, second)
        self.assertEqual(len(self.construction.configs), 2)
        self.assert_preserved(before)
        self.assertNotIn('synthetic-not-a-credential', repr(first))
        self.assertNotIn('synthetic-not-a-credential', repr(self.construction.configs))

    def test_success_preserves_existing_cache_even_for_changed_same_id(self):
        old, other = self.seed()
        before = self.cache_snapshot()
        same = self.factory.prepare(profile())
        changed = self.factory.prepare(profile(model='candidate', api_key='synthetic-rotated'))
        self.assertIsNot(same, old)
        self.assertIsNot(changed, old)
        self.assert_preserved(before)
        self.assertIs(self.factory.build(profile()), old)
        self.assertIs(self.factory.build(profile(id=9, name='Other', model='other')), other)
        self.assertEqual(len(self.construction.configs), 4)

    def test_validation_failure_precedes_constructor_and_keeps_cache(self):
        self.seed()
        before = self.cache_snapshot()
        count = len(self.construction.configs)
        cases = ({'format':'openai-responses'}, {'proxy':'http://proxy.invalid'},
                 {'rate_per_second':1}, {'session_header_key':'Session'},
                 {'format':'anthropic','thinking_type':'enabled'},
                 {'format':'anthropic','reasoning_effort':'xhigh'},
                 {'base_url':'https://user:password@example.invalid'}, {'api_key':''},
                 {'max_tokens':True}, {'streaming':1})
        for changes in cases:
            with self.subTest(fields=tuple(changes)), self.assertRaises(
                    ProtocolError if 'base_url' in changes else ProfileInvalid):
                self.factory.prepare(profile(**changes))
            self.assert_preserved(before)
        self.assertEqual(len(self.construction.configs), count)

    def test_transport_failure_is_redacted_and_does_not_replace_cache(self):
        self.seed(); before = self.cache_snapshot()
        def fail(config): raise RuntimeError('synthetic-credential-should-not-escape')
        self.construction.callback = fail
        with self.assertRaises(ProfileInvalid) as caught:
            self.factory.prepare(profile(model='candidate'))
        self.assertEqual(str(caught.exception), 'transport construction failed')
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assert_preserved(before)

    def test_noncallable_transport_does_not_populate_cache(self):
        factory = ProfileFactory(lambda config: object())
        with self.assertRaisesRegex(ProfileInvalid, 'must return a callable'):
            factory.prepare(profile())
        self.assertEqual(factory._cache, {})

    def test_base_exception_preserves_original_identity_and_cache(self):
        self.seed(); before = self.cache_snapshot()
        abort = AbortConstruction('synthetic interruption')
        def fail(config): raise abort
        self.construction.callback = fail
        with self.assertRaises(AbortConstruction) as caught:
            self.factory.prepare(profile(model='candidate'))
        self.assertIs(caught.exception, abort)
        self.assert_preserved(before)

    def test_partial_pool_construction_failure_has_no_partial_cache_publication(self):
        old, other = self.seed(); before = self.cache_snapshot()
        def fail_second(config):
            if config.model == 'second-fails': raise RuntimeError('synthetic failure')
        self.construction.callback = fail_second
        with self.assertRaises(ProfileInvalid):
            self.factory.prepare_pool([profile(model='first-prepared', is_default=True),
                                       profile(id=2, model='second-fails')])
        self.assertEqual([c.model for c in self.construction.configs[-2:]],
                         ['first-prepared', 'second-fails'])
        self.assert_preserved(before)
        self.assertIs(self.factory._cache[1][1], old)
        self.assertIs(self.factory._cache[9][1], other)

    def test_prepared_pool_preserves_ranking_exclusion_windows_and_health(self):
        self.seed(); before = self.cache_snapshot()
        profiles = [profile(id=8, priority=4), profile(id=3, priority=4, context_window_k=1),
                    profile(id=4, pool_exclude=True),
                    profile(id=1, is_default=True, pool_exclude=True, priority=-9,
                            context_window_k=1000)]
        prepared = self.factory.prepare_pool(profiles)
        self.assertEqual([m.id for m in prepared.members], [1, 3, 8])
        self.assertEqual([m.rank for m in prepared.members], [RANK_ACTIVE, 4, 4])
        self.assertEqual([m.window_tokens for m in prepared.members], [1000000, 32000, 200000])
        self.assertIs(prepared.health, self.health)
        self.assertEqual(prepared._counter, 0)
        self.assertIsNot(prepared.head.provider, self.factory._cache[1][1])
        self.assert_preserved(before)

    def test_pool_snapshot_isolated_before_first_constructor_runs(self):
        originals = [profile(is_default=True), profile(id=2, name='Second', model='before')]
        def mutate_callers(config):
            originals[0].api_key = 'synthetic-mutated'
            originals[1].model = 'after'
            originals[1].name = 'Changed'
            originals[1].context_window_k = 1000
        self.construction.callback = mutate_callers
        pool = self.factory.prepare_pool(originals)
        self.assertEqual([c.model for c in self.construction.configs], ['model', 'before'])
        self.assertEqual(pool.members[1].name, 'Second')
        self.assertEqual(pool.members[1].window_tokens, 200000)
        self.assertEqual(self.construction.configs[0].api_key, 'synthetic-not-a-credential')
        self.assertEqual(self.factory._cache, {})

    def test_profile_snapshot_preserves_config_cap_and_retry_dependencies(self):
        original = profile(max_tokens=17, streaming=False)
        before = deepcopy(original)
        prepared = self.factory.prepare(original)
        original.model = 'changed'; original.max_tokens = 900; original.streaming = True
        self.assertEqual(prepared._provider.provider.config.model, before.model)
        self.assertEqual(prepared._maximum, 17)
        self.assertFalse(prepared._streaming)
        retry = prepared._provider
        self.assertEqual(retry.policy.max_attempts, 7)
        self.assertEqual(retry.policy.max_seconds, 12)
        self.assertEqual(retry.policy.rules, before.retry)
        self.assertIs(retry.clock, self.factory._clock)
        self.assertIs(retry.sleep, self.factory._sleep)
        self.assertIs(retry.provider.transport.policy, retry.policy)
        self.assertEqual(self.factory._cache, {})

    def test_empty_excluded_or_invalid_pool_keeps_cache_and_avoids_construction(self):
        self.seed(); before = self.cache_snapshot(); count = len(self.construction.configs)
        self.assertIsNone(self.factory.prepare_pool([]))
        self.assertIsNone(self.factory.prepare_pool([profile(pool_exclude=True)]))
        for profiles in ([profile(id=0)], [profile(), profile()],
                         [profile(is_default=True), profile(id=2,is_default=True)], [object()]):
            with self.subTest(size=len(profiles)), self.assertRaises(ProfileInvalid):
                self.factory.prepare_pool(profiles)
        self.assertEqual(len(self.construction.configs), count)
        self.assert_preserved(before)

    def test_existing_build_and_pool_still_cache_and_invalidate_by_identity(self):
        original = profile(is_default=True)
        cached = self.factory.build(original)
        pool = self.factory.pool([original])
        self.assertIs(pool.head.provider, cached)
        self.assertEqual(len(self.construction.configs), 1)
        candidate = self.factory.prepare(original)
        self.assertIsNot(candidate, cached)
        self.assertIs(self.factory.pool([original]).head.provider, cached)
        self.factory.invalidate(1)
        self.assertEqual(self.factory._cache, {})
        self.assertIsNot(self.factory.build(original), cached)
        self.assertIs(pool.head.provider, cached)  # Old references are not revoked.
        self.assertIsNot(candidate, self.factory._cache[1][1])


if __name__ == '__main__': unittest.main()
