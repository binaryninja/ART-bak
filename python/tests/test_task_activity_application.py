import io,json,unittest
from contextlib import nullcontext
from artex.server.application import build_application,build_persisted_application
from artex.server.task_activity import TaskActivityService
from artex.db.task_activity_read import TaskActivityReadStore,ActivityReadScope
from artex.server.auth import sign_jwt
from test_tasks_store import Fake
from test_application import TestCodec

class Tests(unittest.TestCase):
    def make(self,db):
        self.calls=[]
        store=TaskActivityReadStore(db,ActivityReadScope(5,9,'r1'),current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),authorize=lambda *a:True,raw_authorize=lambda *a:True)
        return TaskActivityService(store,authorize=lambda *a:self.calls.append(a) or False,stream_current=lambda *a:True)
    def request(self,app,authorized=True):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/exploration/activity','QUERY_STRING':'task=5','wsgi.input':io.BytesIO(b'')}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:statuses.append(s)));return int(statuses[0].split()[0]),json.loads(body)
    def test_no_default_routes(self):
        self.assertEqual(self.request(build_application(Fake([]),b'x'*32,TestCodec()))[0],404)
    def test_explicit_jwt_before_policy_no_constructor_callbacks(self):
        db=Fake([]);service=self.make(db);app=build_application(db,b'x'*32,TestCodec(),task_activity_service=service)
        self.assertEqual(self.calls,[]);self.assertEqual(db.calls,[])
        self.assertEqual(self.request(app,False)[0],401);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app)[0],403);self.assertEqual(db.calls,[])
    def test_original_pins_rejected_before_startup(self):
        mutations=(lambda s,db:setattr(s.store,'executor',Fake([])),lambda s,db:setattr(s.store,'scope',ActivityReadScope(5,9,'r1')),
            lambda s,db:setattr(s.store,'guard',lambda *a:nullcontext()),lambda s,db:setattr(s,'authorize',lambda *a:True),
            lambda s,db:setattr(s.store,'raw_authorize',lambda *a:True),lambda s,db:setattr(s,'max_batches',100))
        for factory in (build_application,build_persisted_application):
            for change in mutations:
                db=Fake([]);service=self.make(db);change(service,db)
                with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),task_activity_service=service)
                self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
            db=Fake([]);service=self.make(Fake([]));service.store.executor=db
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),task_activity_service=service)
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),task_activity_service=object())
            self.assertEqual(db.calls,[])
    def test_persisted_forwarding_keeps_scope(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);service=self.make(db)
        app=build_persisted_application(db,b'x'*32,TestCodec(),task_activity_service=service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app)[0],403)
