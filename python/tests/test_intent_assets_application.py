import unittest
from artex.server.application import build_application,build_persisted_application
from test_pool_application import PoolApplicationTests
from test_task_intent_assets import IntentAssetsTests,PARENT,ROW
from test_tasks_store import Fake
from test_application import TestCodec
class ApplicationTests(unittest.TestCase):
    request=PoolApplicationTests.request
    def fixture(self,rows):
        f=IntentAssetsTests();f.make(rows);return f
    def test_default_absent(self):
        app=build_application(Fake([]),b'x'*32,TestCodec())
        self.assertEqual(self.request(app,'/api/tasks/5/intent-assets')[0],404)
    def test_injected_no_construction_callbacks_and_jwt_first(self):
        f=self.fixture([PARENT,[],[ROW]]);calls=[]
        f.service.authorize=lambda *a:calls.append(a) or True
        app=build_application(f.db,b'x'*32,TestCodec(),intent_assets_service=f.service)
        self.assertEqual(f.db.calls,[]);self.assertEqual(calls,[])
        self.assertEqual(self.request(app,'/api/tasks/5/intent-assets',authorized=False)[0],401);self.assertEqual(calls,[])
        self.assertEqual(self.request(app,'/api/tasks/5/intent-assets')[0],200)
    def test_bad_bindings_before_startup(self):
        for factory in (build_application,build_persisted_application):
            for field in ('executor','scope','guard','policy','store','request'):
                f=self.fixture([])
                if field=='executor':f.coverage.executor=Fake([])
                elif field=='scope':f.coverage.scope=object()
                elif field=='guard':f.coverage.guard=lambda *a:None
                elif field=='policy':f.coverage.authorize=lambda *a:True
                elif field=='store':f.service.store=object()
                else:f.service.authorize=None
                with self.subTest(factory=factory.__name__,field=field),self.assertRaises(ValueError):
                    factory(f.db,b'x'*32,TestCodec(),intent_assets_service=f.service)
                self.assertEqual(f.db.calls,[])
    def test_persisted_forwarding(self):
        f=self.fixture([[],PARENT,[],[ROW]])
        app=build_persisted_application(f.db,b'x'*32,TestCodec(),intent_assets_service=f.service)
        self.assertEqual(len(f.db.calls),1)
        self.assertEqual(self.request(app,'/api/tasks/5/intent-assets')[0],200)
