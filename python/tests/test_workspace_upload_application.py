"""Actual WSGI raw multipart handling; new temporary synthetic workspace only."""
from contextlib import nullcontext
from pathlib import Path
import tempfile,io,json,unittest
from artex.server.application import build_application,build_persisted_application
from artex.server.workspace_upload import WorkspaceUploadService
from artex.server.workspace import WorkspaceBinding
from artex.server.auth import sign_jwt
from test_workspace_upload import body
from test_tasks_store import Fake
from test_application import TestCodec
class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);s=self.root.stat();binding=WorkspaceBinding(5,str(self.root),s.st_dev,s.st_ino,'r')
        self.calls=[]
        def resolve(req):self.calls.append(req.body);return binding
        self.service=WorkspaceUploadService(resolve=resolve,authorize=lambda *a:True,current=lambda b:True,writer_guard=lambda b:nullcontext(),upload_authorize=lambda *a:True)
    def call(self,app,data=None,auth=True):
        data=body() if data is None else data;statuses=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':'/api/workspace/upload','QUERY_STRING':'','CONTENT_TYPE':'multipart/form-data; boundary=fixture','CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data)}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        out=b''.join(app(env,lambda s,h:statuses.append(s)));return int(statuses[0].split()[0]),json.loads(out)
    def test_optin_auth_raw_binary_and_inert_composition(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec(),workspace_upload_service=self.service)
        self.assertEqual(self.calls,[]);self.assertEqual(db.calls,[]);self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(self.call(app,auth=False)[0],401);self.assertEqual(self.calls,[])
        raw=body(data=b'\0\xff\r\nexact');self.assertEqual(self.call(app,raw)[1],{'uploaded':1})
        self.assertEqual(self.calls,[raw]);self.assertEqual((self.root/'x.bin').read_bytes(),b'\0\xff\r\nexact')
    def test_global_limit_not_expanded_before_service_parser(self):
        app=build_application(Fake([]),b'x'*32,TestCodec(),workspace_upload_service=self.service,max_body_bytes=32)
        self.assertEqual(self.call(app)[0],413);self.assertEqual(self.calls,[]);self.assertEqual(list(self.root.iterdir()),[])
    def test_default_absent_invalid_service_before_persisted_reads(self):
        db=Fake([]);self.assertEqual(self.call(build_application(db,b'x'*32,TestCodec()))[0],404)
        class Other(WorkspaceUploadService):
            def register(self,r):raise AssertionError('override')
        for factory in (build_application,build_persisted_application):
            for service in (object(),object.__new__(Other)):
                with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),workspace_upload_service=service)
        self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
    def test_persisted_forwards_without_filesystem_callbacks(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);app=build_persisted_application(db,b'x'*32,TestCodec(),workspace_upload_service=self.service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.calls,[]);self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(self.call(app)[0],200)
