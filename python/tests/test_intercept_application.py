import io,json,unittest
from contextlib import nullcontext
from artex.server.application import build_application,build_persisted_application
from artex.server.intercept_rules import InterceptRuleService
from artex.db.intercept_rules import InterceptRuleStore,RuleScope
from artex.server.auth import sign_jwt
from test_tasks_store import Fake
from test_application import TestCodec

class InterceptApplicationTests(unittest.TestCase):
    def make(self,db,task=False):
        self.calls=[]
        store=InterceptRuleStore(db,RuleScope('explicit',5,9) if task else RuleScope('explicit'),authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.service=InterceptRuleService(store,authorize=lambda *a:self.calls.append(a) or True)
        return self.service
    def request(self,app,path='/api/asset-intercept/rules',authorized=True):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':'','wsgi.input':io.BytesIO(b'')}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:statuses.append(s)));return int(statuses[0].split()[0]),json.loads(body)
    def test_default_routes_absent(self):
        app=build_application(Fake([]),b'x'*32,TestCodec())
        for path in ('/api/asset-intercept/rules','/api/tasks/5/intercept-rules'):
            self.assertEqual(self.request(app,path)[0],404)
    def test_optin_auth_first_and_actual_store(self):
        db=Fake([[]]);app=build_application(db,b'x'*32,TestCodec(),intercept_service=self.make(db))
        self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app,authorized=False)[0],401);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app),(200,{'rules':[]}))
    def test_task_scope_not_retargeted_or_global(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec(),intercept_service=self.make(db,True))
        self.assertEqual(self.request(app,'/api/tasks/6/intercept-rules')[0],403)
        self.assertEqual(self.request(app)[0],404);self.assertEqual(db.calls,[])
    def test_foreign_or_wrong_type_before_persisted_reads(self):
        db=Fake([]);foreign=Fake([])
        for factory in (build_application,build_persisted_application):
            for service in (object(),self.make(foreign)):
                with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),intercept_service=service)
        self.assertEqual(db.calls,[]);self.assertEqual(foreign.calls,[])
    def test_rebound_store_rejected_without_callbacks(self):
        db=Fake([]);service=self.make(db);service.store.executor=Fake([])
        with self.assertRaises(ValueError):build_persisted_application(db,b'x'*32,TestCodec(),intercept_service=service)
        self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
    def test_persisted_forwarding_explicit_policy(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);service=self.make(db);service.authorize=lambda *a:False
        app=build_persisted_application(db,b'x'*32,TestCodec(),intercept_service=service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.request(app)[0],403)
