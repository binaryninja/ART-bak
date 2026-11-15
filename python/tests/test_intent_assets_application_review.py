"""Independent wiring checks; injected inert SQL, no user data."""
import unittest
import test_intent_assets_application as fixture
from artex.server.application import build_application,build_persisted_application
from test_application import TestCodec
from test_tasks_store import Fake
class Review(unittest.TestCase):
    def test_private_original_executor_rejected_before_persisted_reads(self):
        for factory in (build_application,build_persisted_application):
            f=fixture.ApplicationTests().fixture([])
            f.coverage._executor=Fake([])
            with self.assertRaises(ValueError):factory(f.db,b'x'*32,TestCodec(),intent_assets_service=f.service)
            self.assertEqual(f.db.calls,[])
    def test_unauthorized_request_never_runs_source_lifetime_callback(self):
        f=fixture.ApplicationTests().fixture([])
        def forbidden(*args):raise AssertionError('Unauthorized callback')
        f.coverage.current=forbidden
        from artex.db.task_intent_assets import TaskIntentAssetsStore
        from artex.server.task_intent_assets import TaskIntentAssetsService
        service=TaskIntentAssetsService(TaskIntentAssetsStore(f.coverage),authorize=forbidden)
        app=build_application(f.db,b'x'*32,TestCodec(),intent_assets_service=service)
        result=fixture.ApplicationTests.request(self,app,'/api/tasks/5/intent-assets',authorized=False)
        self.assertEqual(result[0],401);self.assertEqual(f.db.calls,[])
