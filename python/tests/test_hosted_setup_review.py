"""Independent hosted ownership/configuration review; inert WSGI/DB fixtures."""
import unittest
from artex.manager import RuntimeUnavailable
import test_hosted_setup as fixtures

class HostedReview(unittest.TestCase):
    setUp=fixtures.HostedSetupTests.setUp
    build=fixtures.HostedSetupTests.build
    request=fixtures.HostedSetupTests.request
    def test_empty_runner_cannot_be_owned_by_second_host(self):
        first=self.build();before=len(self.db.calls)
        with self.assertRaises(RuntimeUnavailable):self.build()
        self.assertEqual(len(self.db.calls),before)
        self.assertIs(first.manager.runner,self.runner)
    def test_failed_startup_does_not_release_runner_for_new_owner(self):
        original=self.db.fetch_all
        self.db.fetch_all=lambda q:(_ for _ in ()).throw(RuntimeError('fixture read failure'))
        with self.assertRaises(RuntimeError):self.build()
        self.db.fetch_all=original
        with self.assertRaises(RuntimeUnavailable):self.build()
    def test_cross_database_engine_cannot_be_admitted(self):
        self.build()
        import test_engine
        foreign=test_engine.DB()
        self.fixture.graph.executor=foreign;self.fixture.worker.store.executor=foreign
        self.assertIsNot(self.db,foreign)
        status,_=self.request('/api/tasks/5/control',{'action':'resume'})
        self.assertEqual(status,503)
        self.assertEqual(self.runner.handles(),())
        self.assertFalse(self.fixture.pp.calls)
        self.assertFalse(self.fixture.wp.calls)
    def test_capacity_denial_prevents_lifecycle_sql(self):
        from contextlib import contextmanager
        @contextmanager
        def deny(operation,task_id):yield False
        self.build(admission_guard=deny);before=len(self.db.calls)
        status,_=self.request('/api/tasks/5/control',{'action':'resume'})
        self.assertEqual(status,503)
        self.assertFalse(any(not q.sql.lstrip().startswith('SELECT') for q in self.db.calls[before:]))
        self.assertEqual(self.runner.handles(),())
    def test_same_host_claim_idempotent_late_storage_swap_rejected(self):
        bundle=self.build();self.runner.claim_host(bundle.host)
        import test_engine
        original=bundle.manager.repository.executor
        bundle.manager.repository.executor=test_engine.DB()
        try:
            with self.assertRaises(RuntimeUnavailable):bundle.host.poll()
        finally:bundle.manager.repository.executor=original
        self.assertEqual(self.runner.handles(),())
