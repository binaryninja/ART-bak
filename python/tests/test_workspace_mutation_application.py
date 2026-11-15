"""Actual WSGI and no-follow mutations in new temporary fixtures only."""
from contextlib import nullcontext
from pathlib import Path
import io,json,tempfile,unittest
from artex.server.application import build_application,build_persisted_application
from artex.server.workspace_mutations import WorkspaceMutationService
from artex.server.workspace import WorkspaceBinding
from artex.server.auth import sign_jwt
from test_tasks_store import Fake
from test_application import TestCodec
class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);s=self.root.stat();binding=WorkspaceBinding(5,str(self.root),s.st_dev,s.st_ino,'r')
        self.calls=[];self.delete=False
        def resolve(req):self.calls.append('resolve');return binding
        self.service=WorkspaceMutationService(resolve=resolve,authorize=lambda *a:True,current=lambda b:True,writer_guard=lambda b:nullcontext(),mutation_authorize=lambda *a:True,delete_authorize=lambda *a:self.delete)
    def request(self,app,method='POST',path='/api/workspace/mkdir',query='',auth=True):
        status=[];data=b'{"path":"new"}' if method=='POST' else b''
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data)}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:status.append(s)));return int(status[0].split()[0]),json.loads(body)
    def test_inert_composition_auth_and_separate_delete_grant(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec(),workspace_mutation_service=self.service)
        self.assertEqual(self.calls,[]);self.assertEqual(db.calls,[]);self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(self.request(app,auth=False)[0],401);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app)[1],{'ok':True,'path':'new'});self.assertTrue((self.root/'new').is_dir())
        self.assertEqual(self.request(app,'DELETE','/api/workspace/delete','path=new')[0],403);self.assertTrue((self.root/'new').exists())
        self.delete=True;self.assertEqual(self.request(app,'DELETE','/api/workspace/delete','path=new')[1],{'ok':True});self.assertFalse((self.root/'new').exists())
        self.assertEqual(db.calls,[])
    def test_absent_defaults_and_subclass_reject_before_sql(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec())
        self.assertEqual(self.request(app)[0],404);self.assertEqual(self.request(app,'DELETE','/api/workspace/delete','path=new')[0],404)
        class Wrong(WorkspaceMutationService):
            def register(self,r):raise AssertionError('override')
        for factory in (build_application,build_persisted_application):
            for value in (object(),object.__new__(Wrong)):
                with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),workspace_mutation_service=value)
        self.assertEqual(db.calls,[]);self.assertEqual(self.calls,[])
    def test_persisted_forwards_without_resolving_workspace(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);app=build_persisted_application(db,b'x'*32,TestCodec(),workspace_mutation_service=self.service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.calls,[]);self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(self.request(app)[0],200)
