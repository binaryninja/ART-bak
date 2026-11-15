import json
import unittest
from copy import deepcopy
from artex.db.profiles import LLMProfile, RetryOverride, RetryRule, ProfileInvalid
from artex.providers import Response
from artex.providers.factory import ProfileFactory, ProfileCoordinator, ApplyPending


def profile(**kw):
    values = dict(id=1, name='test', model='model', api_key='private-key',
                  retry=RetryOverride(*[RetryRule(-1, 0)] * 3))
    values.update(kw)
    return LLMProfile(**values)


class Store:
    def __init__(self, profiles):
        self.items = {p.id: deepcopy(p) for p in profiles}
        self.writes = 0
        self.fail_read = False
    def get(self, identifier): return deepcopy(self.items.get(identifier))
    def active(self):
        if self.fail_read: raise RuntimeError('secret database error')
        return next((deepcopy(p) for p in self.items.values() if p.is_default), None)
    def pool(self): return deepcopy(list(self.items.values()))
    def save(self, p):
        self.writes += 1
        p = deepcopy(p)
        if not p.api_key and p.id in self.items: p.api_key = self.items[p.id].api_key
        if not p.id: p.id = max(self.items, default=0)+1
        p.is_default = self.items[p.id].is_default if p.id in self.items else False
        self.items[p.id] = p
        return p.id
    def set_active(self, identifier):
        self.writes += 1
        for p in self.items.values(): p.is_default = p.id == identifier


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.constructed, self.calls = [], []
        def construct(config):
            self.constructed.append(config)
            def transport(context, url, headers, body, stream):
                self.calls.append((url, headers, json.loads(body), stream))
                return Response(200, json.dumps({'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}))
            return transport
        self.factory = ProfileFactory(construct)

    def test_lazy_cache_private_key_and_request_cap(self):
        p = profile(max_tokens=80)
        provider = self.factory.build(p)
        self.assertIs(provider, self.factory.build(p))
        self.assertFalse(self.calls)
        self.assertNotIn(p.api_key, repr(self.constructed[0]))
        request = {'messages': [], 'max_tokens': 100}
        provider.complete(None, request)
        self.assertEqual(self.calls[0][2]['max_tokens'], 80)
        self.assertEqual(request['max_tokens'], 100)
        self.assertEqual(self.calls[0][1]['authorization'], 'Bearer private-key')
        p.api_key = 'rotated'
        self.assertIsNot(provider, self.factory.build(p))
        self.factory.invalidate(1)
        self.factory.build(p)
        self.assertEqual(len(self.constructed), 3)

    def test_unsupported_settings_fail_before_transport(self):
        for kwargs in ({'format':'openai-responses'}, {'proxy':'http://proxy.test'},
                       {'rate_per_second':1}, {'session_header_key':'Session'},
                       {'retry':RetryOverride()}, {'format':'anthropic','thinking_type':'enabled'},
                       {'format':'anthropic','reasoning_effort':'xhigh'},
                       {'base_url':'https://user:password@test.invalid'}, {'api_key':''}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.factory.build(profile(**kwargs))
        self.assertFalse(self.constructed)

    def test_stream_disabled(self):
        provider = self.factory.build(profile(streaming=False))
        with self.assertRaises(ProfileInvalid): provider.stream(None, {'messages':[]})
        self.assertFalse(self.calls)
        provider.complete(None, {'messages':[]})

    def test_pool_order_exclusion_and_health(self):
        pool = self.factory.pool([profile(id=2,priority=4), profile(id=3,pool_exclude=True),
                                  profile(id=1,is_default=True,priority=-5)])
        self.assertEqual([m.id for m in pool.members], [1,2])
        self.assertIs(pool.health,self.factory.health)
        self.assertEqual(pool.head.window_tokens,200000)
        self.assertEqual(pool.complete(None,{'messages':[]})[0]['content'][0]['text'], 'ok')
        with self.assertRaises(ProfileInvalid): self.factory.pool([profile(),profile()])

    def test_runtime_snapshot_and_preserved_key_save(self):
        store = Store([profile(is_default=True)])
        coordinator = ProfileCoordinator(store,self.factory)
        coordinator.reload()
        self.assertTrue(coordinator.snapshot()['configured'])
        self.assertNotIn('private-key',repr(coordinator.snapshot()))
        replacement = profile(api_key='',model='changed')
        coordinator.save(replacement)
        self.assertEqual(store.get(1).api_key,'private-key')
        self.assertEqual(coordinator.snapshot()['model'],'changed')
        self.assertEqual(replacement.api_key,'')

    def test_failure_after_save_is_pending_and_clears_old_runtime(self):
        store = Store([profile(is_default=True)])
        coordinator = ProfileCoordinator(store,self.factory)
        coordinator.reload()
        store.fail_read=True
        with self.assertRaises(ApplyPending) as error: coordinator.save(profile(model='new'))
        self.assertEqual(store.writes,1)
        self.assertEqual(store.get(1).model,'new')
        self.assertNotIn('secret',str(error.exception))
        self.assertEqual(coordinator.runtime()[0],None)
        self.assertFalse(coordinator.snapshot()['configured'])

    def test_preflight_before_persistence(self):
        store = Store([profile(is_default=True)])
        coordinator = ProfileCoordinator(store,self.factory)
        with self.assertRaises(ProfileInvalid): coordinator.save(profile(proxy='http://proxy.invalid'))
        self.assertEqual(store.writes,0)

    def test_server_pending_type_and_public_dto(self):
        from artex.server.profiles import ProfileApplyPending, runtime_dto
        store = Store([profile(is_default=True)])
        coordinator = ProfileCoordinator(store,self.factory,pending_error=ProfileApplyPending)
        coordinator.reload()
        self.assertEqual(runtime_dto(coordinator.snapshot()),coordinator.snapshot())
        store.fail_read=True
        with self.assertRaises(ProfileApplyPending): coordinator.set_active(1)

    def test_activation_and_inconsistent_read(self):
        store = Store([profile(is_default=True),profile(id=2)])
        coordinator = ProfileCoordinator(store,self.factory)
        coordinator.set_active(2)
        self.assertEqual(coordinator.runtime()[0].head.id,2)
        store.pool=lambda: []
        with self.assertRaises(ProfileInvalid): coordinator.reload()
        self.assertIsNone(coordinator.runtime()[0])

    def test_transport_construction_redacted(self):
        def fail(config): raise RuntimeError('private-key')
        factory=ProfileFactory(fail)
        with self.assertRaises(ProfileInvalid) as error: factory.build(profile())
        self.assertNotIn('private-key',str(error.exception))


if __name__ == '__main__': unittest.main()
