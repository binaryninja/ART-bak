import unittest
from contextlib import nullcontext
from artex.server.application import build_application,build_persisted_application
from artex.server.intercept_inspection import InterceptInspectionService
from artex.db.intercept_history import InterceptHistoryStore,InspectionScope
from test_pool_application import PoolApplicationTests
from test_tasks_store import Fake
from test_application import TestCodec
class InspectionApplicationTests(unittest.TestCase):
    request=PoolApplicationTests.request
    def make(self,db):
        self.calls=[]
        store=InterceptHistoryStore(db,InspectionScope('workspace','task-text'),authorize=lambda *a:self.calls.append(a) or True,raw_authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        return InterceptInspectionService(store,authorize=lambda *a:True)
    def test_no_defaults(self):
        app=build_application(Fake([]),b'x'*32,TestCodec())
        for path in ('/api/intercept/pending','/api/intercept/history'):
            self.assertEqual(self.request(app,path)[0],404)
    def test_jwt_before_policy_and_no_constructor_sql(self):
        db=Fake([[(0,)+(None,)*15]]);service=self.make(db)
        app=build_application(db,b'x'*32,TestCodec(),intercept_inspection_service=service)
        self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app,'/api/intercept/pending',authorized=False)[0],401);self.assertEqual(db.calls,[])
        self.assertEqual(self.request(app,'/api/intercept/pending'),(200,{'pending':[]}))
    def test_resource_pins_before_startup(self):
        for factory in (build_application,build_persisted_application):
            for field in ('type','executor','scope','guard','store'):
                db=Fake([]);service=self.make(db)
                if field=='type':service=object()
                elif field=='executor':service.store.executor=Fake([])
                elif field=='scope':service.store.scope=InspectionScope('foreign')
                elif field=='guard':service.store.guard=lambda *a:nullcontext()
                else:service.store=self.make(db).store
                with self.subTest(factory=factory.__name__,field=field),self.assertRaises(ValueError):
                    factory(db,b'x'*32,TestCodec(),intercept_inspection_service=service)
                self.assertEqual(db.calls,[])
    def test_persisted_forwarding_no_decision_route(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([[(0,)+(None,)*15]]);s=self.make(db)
        app=build_persisted_application(db,b'x'*32,TestCodec(),intercept_inspection_service=s)
        self.assertEqual(len(db.calls),1)
        self.assertEqual(self.request(app,'/api/intercept/pending')[0],200)
        self.assertIn(self.request(app,'/api/intercept/pending/7/decide',method='POST',body=b'{}')[0],(404,405))
