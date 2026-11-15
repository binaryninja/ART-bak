"""Independent visibility assembly identity checks; fake SQL only."""
from contextlib import nullcontext
import unittest
import test_visibility_application as fixture
from artex.server.application import build_application,build_persisted_application
from artex.db.visibility import VisibilityScope
from test_application import TestCodec


class Review(unittest.TestCase):
    def test_original_service_scope_cannot_be_replaced_as_consistent_pair(self):
        f=fixture.Tests();f.setUp()
        original=f.db.fetch_all
        def read(q):
            if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:
                f.db.calls.append(q);return []
            return original(q)
        f.db.fetch_all=read
        f.store.scope=f.store._scope=VisibilityScope('replacement-catalog','r2')
        for factory in (build_application,build_persisted_application):
            with self.subTest(factory=factory.__name__):
                with self.assertRaises(ValueError):
                    factory(f.db,b'x'*32,TestCodec(),visibility_service=f.service)
        self.assertEqual(f.db.calls,[])

    def test_original_service_guard_cannot_be_replaced_as_consistent_pair(self):
        f=fixture.Tests();f.setUp()
        original=f.db.fetch_all
        def read(q):
            if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:
                f.db.calls.append(q);return []
            return original(q)
        f.db.fetch_all=read
        f.store.guard=f.store._guard=lambda scope:nullcontext()
        for factory in (build_application,build_persisted_application):
            with self.subTest(factory=factory.__name__):
                with self.assertRaises(ValueError):
                    factory(f.db,b'x'*32,TestCodec(),visibility_service=f.service)
        self.assertEqual(f.db.calls,[])
