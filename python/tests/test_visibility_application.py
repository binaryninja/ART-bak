"""Optional visibility assembly; synthetic store/executor only."""
from contextlib import nullcontext
import io,json,unittest
from artex.server.application import build_application,build_persisted_application
from artex.server.visibility import VisibilityService
from artex.db.visibility import VisibilityStore,VisibilityScope
from artex.server.auth import sign_jwt
from test_visibility import DB
from test_application import TestCodec
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.calls=[]
        self.store=VisibilityStore(self.db,VisibilityScope('catalog','r'),authorize=lambda *a:True,current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),mutation_authorize=lambda *a:True)
        self.service=VisibilityService(self.store,authorize=lambda *a:self.calls.append(a) or True)
    def request(self,app,auth=True,path='/api/visibility/mcp/3',data=None):
        raw=b'' if data is None else json.dumps(data).encode();statuses=[]
        env={'REQUEST_METHOD':'GET' if data is None else 'POST','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        out=b''.join(app(env,lambda s,h:statuses.append(s)));return int(statuses[0].split()[0]),json.loads(out)
    def test_optin_auth_and_synthetic_metadata_no_seed(self):
        app=build_application(self.db,b'x'*32,TestCodec(),visibility_service=self.service)
        self.assertEqual(self.calls,[]);self.assertEqual(self.db.calls,[])
        self.assertEqual(self.request(app,False)[0],401);self.assertEqual(self.calls,[]);self.assertEqual(self.db.calls,[])
        self.assertEqual(self.request(app)[1],{'agents':['2','2']});self.assertEqual(self.db.writes,[])
        self.assertEqual(self.request(app,path='/api/visibility/skill/toggle',data={'agent_id':'2','skill_name':'demo','visible':True})[1],{'ok':True})
        self.assertEqual(len(self.db.writes),1)
    def test_default_routes_absent(self):
        app=build_application(self.db,b'x'*32,TestCodec())
        for path,data in (('/api/visibility/mcp/3',None),('/api/visibility/skill/demo',None),('/api/visibility/toggle',{}),('/api/visibility/skill/toggle',{})):
            self.assertEqual(self.request(app,path=path,data=data)[0],404)
        self.assertEqual(self.db.calls,[])
    def test_rebinding_and_subclass_before_persisted_reads(self):
        for field,value in (('executor',DB()),('scope',VisibilityScope('other','r')),('guard',lambda s:nullcontext())):
            old=getattr(self.store,field);setattr(self.store,field,value)
            for factory in (build_application,build_persisted_application):
                with self.assertRaises(ValueError):factory(self.db,b'x'*32,TestCodec(),visibility_service=self.service)
            setattr(self.store,field,old)
        class Other(VisibilityService):
            def register(self,r):raise AssertionError('override')
        with self.assertRaises(ValueError):build_application(self.db,b'x'*32,TestCodec(),visibility_service=object.__new__(Other))
        with self.assertRaises(ValueError):build_application(DB(),b'x'*32,TestCodec(),visibility_service=self.service)
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.calls,[])
    def test_persisted_forward_only_startup_task_read(self):
        original=self.db.fetch_all
        def read(q):
            if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.db.calls.append(q);return []
            return original(q)
        self.db.fetch_all=read
        app=build_persisted_application(self.db,b'x'*32,TestCodec(),visibility_service=self.service)
        self.assertEqual(len(self.db.calls),1);self.assertEqual(self.calls,[]);self.assertEqual(self.db.writes,[])
        self.assertEqual(self.request(app)[0],200)
    def test_consistent_rebind_in_request_policy_blocks_before_sql(self):
        for field,value in (('scope',VisibilityScope('other','r')),('guard',lambda s:nullcontext())):
            with self.subTest(field=field):
                self.setUp()
                app=build_application(self.db,b'x'*32,TestCodec(),visibility_service=self.service)
                def policy(*args):
                    setattr(self.store,field,value);setattr(self.store,'_'+field,value)
                    return True
                self.service.authorize=policy
                self.assertEqual(self.request(app)[0],503)
                self.assertEqual(self.db.calls,[]);self.assertEqual(self.db.writes,[])
