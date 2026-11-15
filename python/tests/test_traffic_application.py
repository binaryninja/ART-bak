import io,json,tempfile,unittest
from contextlib import nullcontext
from artex.server.application import build_application,build_persisted_application
from artex.server.traffic_read import TrafficReadService
from artex.server.auth import sign_jwt
from artex.traffic import Traffic
from test_tasks_store import Fake
from test_application import TestCodec

class TrafficApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Traffic(self.temp.name);self.addCleanup(self.store.close)
        self.calls=[]
        self.service=TrafficReadService(self.store,directory=self.temp.name,scope='explicit-whole-workspace',authorize=lambda *a:self.calls.append(a[1]) or True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
    def request(self,app,authorized=True,path='/api/traffic'):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':'','wsgi.input':io.BytesIO(b'')}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:statuses.append(s)))
        return int(statuses[0].split()[0]),json.loads(body)
    def test_optional_and_auth_before_traffic_policy(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec(),traffic_service=self.service)
        self.assertEqual(self.calls,[]);self.assertEqual(db.calls,[])
        self.assertEqual(self.request(app,False)[0],401);self.assertEqual(self.calls,[])
        status,data=self.request(app);self.assertEqual(status,200);self.assertEqual(data['exchanges'],[]);self.assertEqual(self.calls,['list','list'])
    def test_absent_by_default(self):
        app=build_application(Fake([]),b'x'*32,TestCodec())
        for path in ['/api/traffic','/api/traffic/hosts','/api/traffic/exchange','/api/traffic/blob']:
            self.assertEqual(self.request(app,path=path)[0],404)
        self.assertEqual(self.calls,[])
    def test_wrong_service_rejected_before_persisted_reads(self):
        db=Fake([])
        for factory in [build_application,build_persisted_application]:
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),traffic_service=object())
        self.assertEqual(db.calls,[])
    def test_persisted_forwarding_and_request_denial(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);app=build_persisted_application(db,b'x'*32,TestCodec(),traffic_service=self.service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.calls,[])
        self.service.authorize=lambda *a:False
        self.assertEqual(self.request(app)[0],403)
