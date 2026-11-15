"""Independent optional traffic composition checks; new temporary SQLite only."""
import unittest
import test_traffic_application as fixture
from artex.server.application import build_application,build_persisted_application
from artex.server.traffic_read import TrafficReadService
from test_tasks_store import Fake
from test_application import TestCodec
class Review(unittest.TestCase):
    def fixture(self):
        f=fixture.TrafficApplicationTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_all_routes_require_auth_before_workspace_policy(self):
        f=self.fixture();app=build_application(Fake([]),b'x'*32,TestCodec(),traffic_service=f.service)
        for path in ('/api/traffic','/api/traffic/hosts','/api/traffic/exchange','/api/traffic/blob'):
            self.assertEqual(f.request(app,False,path)[0],401)
        self.assertEqual(f.calls,[])
    def test_subclass_register_override_rejected_before_startup(self):
        class Other(TrafficReadService):
            def register(self,router):raise AssertionError('override executed')
        other=object.__new__(Other);db=Fake([])
        for factory in (build_application,build_persisted_application):
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),traffic_service=other)
        self.assertEqual(db.calls,[])
    def test_current_scope_failure_blocks_after_composition(self):
        f=self.fixture();app=build_application(Fake([]),b'x'*32,TestCodec(),traffic_service=f.service)
        f.service.current=lambda *a:False
        self.assertEqual(f.request(app)[0],403)
