import io,json,unittest
from contextlib import nullcontext
from artex.server.application import build_application,build_persisted_application
from artex.server.command_history import CommandHistoryService
from artex.db.command_history import CommandHistoryStore,CommandScope
from artex.server.auth import sign_jwt
from test_tasks_store import Fake
from test_application import TestCodec

class CommandApplicationTests(unittest.TestCase):
    def make(self,db,task=False):
        self.calls=[]
        store=CommandHistoryStore(db,CommandScope('explicit',5,9) if task else CommandScope('explicit'),authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.service=CommandHistoryService(store,authorize=lambda *a:self.calls.append(a) or True)
        return self.service
    def request(self,app,path='/api/commands/stats',query='',authorized=True):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':query,'wsgi.input':io.BytesIO(b'')}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:statuses.append(s)));return int(statuses[0].split()[0]),json.loads(body)
    def test_no_default_routes(self):
        app=build_application(Fake([]),b'x'*32,TestCodec())
        for path in ('/api/commands','/api/commands/stats'):self.assertEqual(self.request(app,path)[0],404)
    def test_optional_jwt_before_service_policy(self):
        db=Fake([[(False,None,None,None)]]);app=build_application(db,b'x'*32,TestCodec(),command_history_service=self.make(db))
        self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app,authorized=False)[0],401);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app),(200,{'stats':[]}))
    def test_fixed_task_never_global_or_retarget(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec(),command_history_service=self.make(db,True))
        self.assertEqual(self.request(app)[0],400)
        self.assertEqual(self.request(app,'/api/commands','task=6')[0],403)
        self.assertEqual(self.request(app,'/api/commands')[0],400);self.assertEqual(db.calls,[])
    def test_type_executor_and_original_scope_before_startup(self):
        for factory in (build_application,build_persisted_application):
            db=Fake([]);foreign=Fake([])
            for service in (object(),self.make(foreign)):
                with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),command_history_service=service)
            s=self.make(db).store;s.scope=CommandScope('replacement');service=CommandHistoryService(s,authorize=lambda *a:True)
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),command_history_service=service)
            s=self.make(foreign).store;s.executor=db;service=CommandHistoryService(s,authorize=lambda *a:True)
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),command_history_service=service)
            self.assertEqual(db.calls,[]);self.assertEqual(foreign.calls,[])
    def test_persisted_forward_without_inferred_grant(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);service=self.make(db);service.authorize=lambda *a:False
        app=build_persisted_application(db,b'x'*32,TestCodec(),command_history_service=service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.request(app)[0],403)
