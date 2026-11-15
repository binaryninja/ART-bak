"""Independent fixed-store application assembly regressions; inert SQL only."""
import unittest
from contextlib import nullcontext
from artex.server.application import build_application, build_persisted_application
from artex.server.intercept_rules import InterceptRuleService
from artex.db.intercept_rules import InterceptRuleStore, RuleScope
from test_tasks_store import Fake
from test_application import TestCodec

class InterceptApplicationReview(unittest.TestCase):
    def store(self, db):
        def forbidden(*args):
            raise AssertionError('Assembly must not invoke authorization callbacks')
        return InterceptRuleStore(db, RuleScope('fixed'), authorize=forbidden,
            current=forbidden, lifecycle_guard=forbidden)

    def test_store_rebound_before_service_construction_rejected_before_reads(self):
        for field in ('executor', 'scope'):
            for factory in (build_application, build_persisted_application):
                with self.subTest(field=field, factory=factory.__name__):
                    original = Fake([[]])
                    store = self.store(original)
                    if field == 'executor':
                        db = Fake([[]])
                        store.executor = db
                    else:
                        db = original
                        store.scope = RuleScope('different', 5, 9)
                    service = InterceptRuleService(store, authorize=lambda *a: True)
                    with self.assertRaises(ValueError):
                        factory(db, b'x'*32, TestCodec(), intercept_service=service)
                    self.assertEqual(db.calls, [])
                    self.assertEqual(original.calls, [])

    def test_intact_construction_does_not_call_any_policy(self):
        db = Fake([])
        service = InterceptRuleService(self.store(db), authorize=lambda *a: self.fail('request callback'))
        build_application(db, b'x'*32, TestCodec(), intercept_service=service)
        self.assertEqual(db.calls, [])

    def test_guard_replacement_after_service_creation_rejected_before_reads(self):
        db = Fake([])
        service = InterceptRuleService(self.store(db), authorize=lambda *a: True)
        service.store.guard = lambda *a: nullcontext()
        with self.assertRaises(ValueError):
            build_persisted_application(db, b'x'*32, TestCodec(), intercept_service=service)
        self.assertEqual(db.calls, [])
