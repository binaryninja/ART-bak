import io,json,unittest
from contextlib import nullcontext
from artex.server.application import build_application,build_persisted_application
from artex.server.pool_health import PoolHealthService
from artex.db.settings import SettingsStore
from artex.health import Registry
from artex.server.auth import sign_jwt
from test_tasks_store import Fake
from test_application import TestCodec
class PoolApplicationTests(unittest.TestCase):
    def make(self,db):
        self.calls=[]
        return PoolHealthService(SettingsStore(db),Registry(),scope='explicit-global',authorize=lambda *a:self.calls.append(a) or True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
    def request(self,app,path='/api/llm/pool',authorized=True,method='GET',body=b''):
        statuses=[];env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','wsgi.input':io.BytesIO(body),'CONTENT_LENGTH':str(len(body))}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        response=b''.join(app(env,lambda s,h:statuses.append(s)));return int(statuses[0].split()[0]),json.loads(response)
    def test_default_routes_absent(self):
        app=build_application(Fake([]),b'x'*32,TestCodec())
        for path in ('/api/llm/pool','/api/llm/retry-policy'):self.assertEqual(self.request(app,path)[0],404)
    def test_auth_first_no_construction_effects(self):
        db=Fake([[],[],[]]);s=self.make(db);app=build_application(db,b'x'*32,TestCodec(),pool_health_service=s)
        self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app,authorized=False)[0],401);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app),(200,dict(enabled=False,bind_fallback=False,chain=[])))
    def test_type_foreign_executor_and_resource_pins_prestartup(self):
        for factory in (build_application,build_persisted_application):
            db=Fake([])
            for mutation in ('type','executor','registry','guard','scope','settings'):
                service=self.make(db)
                if mutation=='type':service=object()
                elif mutation=='executor':service.settings.executor=Fake([])
                elif mutation=='registry':service.registry=Registry()
                elif mutation=='guard':service.guard=lambda *a:nullcontext()
                elif mutation=='scope':service.scope='other'
                else:service.settings=SettingsStore(db)
                with self.subTest(factory=factory.__name__,mutation=mutation),self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),pool_health_service=service)
            self.assertEqual(db.calls,[])
    def test_persisted_forwarding_without_write_grants(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);s=self.make(db);app=build_persisted_application(db,b'x'*32,TestCodec(),pool_health_service=s)
        self.assertEqual(len(db.calls),1)
        self.assertEqual(self.request(app,'/api/llm/pool/reset',method='POST',body=b'{"id":7}')[0],503)
        self.assertEqual(len(db.calls),1)
