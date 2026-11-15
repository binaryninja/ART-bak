import unittest
from artex.server.application import build_application,build_persisted_application
from test_pool_application import PoolApplicationTests
from test_main_sessions import Tests as Fixture
from test_tasks_store import Fake
from test_application import TestCodec
class MainSessionApplicationTests(unittest.TestCase):
    request=PoolApplicationTests.request
    def make(self,rows):
        f=Fixture();f.make(rows);return f
    def test_no_default_registration(self):
        app=build_application(Fake([]),b'x'*32,TestCodec())
        self.assertEqual(self.request(app,'/api/exploration/main-sessions')[0],404)
    def test_injected_no_callbacks_or_sql_and_jwt_first(self):
        f=self.make([]);calls=[];f.service.authorize=lambda *a:calls.append(a) or True
        app=build_application(f.db,b'x'*32,TestCodec(),main_sessions_service=f.service)
        self.assertEqual(calls,[]);self.assertEqual(f.db.calls,[])
        self.assertEqual(self.request(app,'/api/exploration/main-sessions',authorized=False)[0],401)
        self.assertEqual(calls,[]);self.assertEqual(f.db.calls,[])
    def test_invalid_pin_before_startup(self):
        for factory in (build_application,build_persisted_application):
            for field in ('executor','scope','guard','request','current'):
                f=self.make([])
                if field=='executor':f.store.executor=Fake([])
                elif field=='scope':f.store.scope=object()
                elif field=='guard':f.store.guard=lambda *a:None
                elif field=='request':f.service.authorize=None
                else:f.store.current=None
                with self.subTest(factory=factory.__name__,field=field),self.assertRaises(ValueError):factory(f.db,b'x'*32,TestCodec(),main_sessions_service=f.service)
                self.assertEqual(f.db.calls,[])
    def test_persisted_forwarding_does_not_install_new_session(self):
        f=self.make([[]]);app=build_persisted_application(f.db,b'x'*32,TestCodec(),main_sessions_service=f.service)
        self.assertEqual(len(f.db.calls),1)
        self.assertEqual(self.request(app,'/api/exploration/main-sessions')[0],400)
        self.assertIn(self.request(app,'/api/exploration/main-session/new',method='POST')[0],(404,405))
