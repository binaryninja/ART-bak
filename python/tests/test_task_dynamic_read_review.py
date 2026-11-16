"""Independent inert dynamic read composition regressions; no providers or targets."""
import unittest
from unittest.mock import patch
import test_task_runtime_read_router as router_fixture
from artex.server import exploration


class DynamicReadReview(unittest.TestCase):
    def setUp(self):
        self.f = router_fixture.Tests(); self.f.setUp()

    def test_builder_policy_replacement_during_selector_cannot_supply_grant(self):
        f = self.f
        policies = list(f.builder._policies)
        policies[0] = lambda req, scope, op: False
        # Pin the configured deny through a freshly constructed builder.
        from artex.server.task_read_builder import TaskReadBuilder
        from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
        from artex.server.application import build_application
        from test_application import TestCodec
        names = ('request_authorize','context_authorize','asset_authorize','runtime_snapshot',
                 'activity_authorize','activity_raw_authorize','activity_request_authorize',
                 'stream_authorize','stream_wait')
        f.builder = TaskReadBuilder(f.db, **dict(zip(names, policies)))
        def selector(req, task_id):
            replacement = list(f.builder._policies)
            replacement[0] = lambda *a: True
            f.builder._policies = tuple(replacement)
            return True
        f.router = TaskRuntimeReadRouter(f.tasks, f.builder, revision='r', authorize_selector=selector,
                                        current=lambda s: True, lifecycle_guard=lambda s: __import__('contextlib').nullcontext())
        f.app = f.router.bind(build_application(f.db, b'x'*32, TestCodec()))
        status, _ = f.request('/api/exploration/intents', 'task=5')
        self.assertNotEqual(status, 200)
        self.assertEqual(f.fetches, [])

    def test_selector_revoked_during_serialization_blocks_disclosure(self):
        f = self.f
        original = exploration.json_response
        def serialize(*a, **kw):
            response = original(*a, **kw)
            f.denied.add(5)
            return response
        with patch.object(exploration, 'json_response', serialize):
            status, _ = f.request('/api/exploration/intents', 'task=5')
        self.assertEqual(status, 403)

    def test_delete_during_projection_blocks_disclosure(self):
        f = self.f
        original = exploration.json_response
        def serialize(*a, **kw):
            response = original(*a, **kw)
            f.db.scopes.pop(5)
            return response
        with patch.object(exploration, 'json_response', serialize):
            status, _ = f.request('/api/exploration/intents', 'task=5')
        self.assertNotEqual(status, 200)

    def test_independent_request_denial_does_not_inherit_selector_grant(self):
        f = self.f
        from artex.server.task_read_builder import TaskReadBuilder
        from artex.server.task_runtime_read_router import TaskRuntimeReadRouter
        from artex.server.application import build_application
        from test_application import TestCodec
        names = ('request_authorize','context_authorize','asset_authorize','runtime_snapshot',
                 'activity_authorize','activity_raw_authorize','activity_request_authorize',
                 'stream_authorize','stream_wait')
        policies = dict(zip(names, f.builder._policies)); policies['request_authorize'] = lambda *a: False
        builder = TaskReadBuilder(f.db, **policies)
        router = TaskRuntimeReadRouter(f.tasks, builder, revision='r', authorize_selector=lambda *a:True,
                current=lambda s:True,lifecycle_guard=lambda s:__import__('contextlib').nullcontext())
        f.app = router.bind(build_application(f.db,b'x'*32,TestCodec()))
        self.assertEqual(f.request('/api/exploration/intents','task=5')[0],403)
        self.assertFalse(any('exploration_nodes' in q.sql for q in f.db.calls))

    def test_authentication_binding_change_during_projection_blocks_disclosure(self):
        f = self.f
        from artex.server.application import build_application
        from test_application import TestCodec
        base = build_application(f.db, b'x'*32, TestCodec())
        f.app = f.router.bind(base)
        original = exploration.json_response
        def serialize(*a, **kw):
            response = original(*a, **kw)
            base.router.authorize = lambda req: True
            return response
        with patch.object(exploration, 'json_response', serialize):
            status, _ = f.request('/api/exploration/intents', 'task=5')
        self.assertNotEqual(status, 200)

    def test_profiles_authentication_binding_change_blocks_disclosure(self):
        f = self.f
        from artex.server.application import build_application
        from artex.server import profiles
        from test_application import TestCodec
        base = build_application(f.db,b'x'*32,TestCodec()); f.app = f.router.bind(base)
        original = profiles.json_response
        def serialize(*a, **kw):
            response=original(*a, **kw); base.router.authorize=lambda req:True
            return response
        with patch.object(profiles,'json_response',serialize):
            status,_=f.request('/api/llm/profiles')
        self.assertNotEqual(status,200)
        self.assertEqual(f.fetches,[])
