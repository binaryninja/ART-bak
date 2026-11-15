"""Independent inert read-boundary checks; no asset or target execution."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from test_task_intent_assets import IntentAssetsTests,PARENT,ROW
import artex.server.task_intent_assets as module
class Review(unittest.TestCase):
    def fixture(self):
        f=IntentAssetsTests();f.make([PARENT,[],[ROW]]);return f
    def test_request_revoked_during_serialization(self):
        f=self.fixture();original=module.json_response
        def serialize(*a,**kw):
            response=original(*a,**kw);f.request_allowed[0]=False;return response
        with patch.object(module,'json_response',serialize):response=f.call()
        self.assertEqual(response.status,403);self.assertNotIn(b'192.0.2.1',response.body)
    def test_request_revoked_at_transaction_exit(self):
        f=self.fixture();original=f.db.transaction
        @contextmanager
        def transaction():
            with original() as tx:yield tx
            f.request_allowed[0]=False
        f.db.transaction=transaction
        response=f.call();self.assertEqual(response.status,403);self.assertNotIn(b'192.0.2.1',response.body)
    def test_nonreentrant_guard_positive(self):
        f=IntentAssetsTests();entered=[False]
        @contextmanager
        def guard(*a):
            if entered[0]:raise AssertionError('Nested guard')
            entered[0]=True
            try:yield
            finally:entered[0]=False
        f.make([PARENT,[],[ROW]])
        from artex.db.task_intent_assets import TaskIntentAssetsStore
        f.coverage.guard=guard;f.store=TaskIntentAssetsStore(f.coverage)
        f.service=module.TaskIntentAssetsService(f.store,authorize=lambda *a:True)
        self.assertEqual(f.call().status,200)
