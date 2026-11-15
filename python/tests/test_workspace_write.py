"""Mutation HTTP tests restricted to newly created temporary fixtures."""
from contextlib import nullcontext
import hashlib,json,os,stat,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from artex.server.core import Request,Router
from artex.server.workspace import WorkspaceBinding
from artex.server.workspace_write import WorkspaceWriteService
class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        st=self.root.stat();self.binding=WorkspaceBinding(5,str(self.root),st.st_dev,st.st_ino,'rev')
        self.valid=True;self.allow=True;self.proposals=[];self.resolutions=0
        self.router=self.make()
    def make(self,**kw):
        def resolve(req):self.resolutions+=1;return self.binding
        def grant(req,b,p):self.proposals.append(p);return self.allow
        opts=dict(resolve=resolve,authorize=lambda *a:True,current=lambda b:self.valid,writer_guard=lambda b:nullcontext(),mutation_authorize=grant)
        opts.update(kw);self.service=WorkspaceWriteService(**opts);router=Router(lambda req:req.header('Authorization')=='yes');self.service.register(router);return router
    def call(self,body=None,raw=None,auth='yes',query=''):
        if body is None:body={'path':'x','content':'hello'}
        return self.router.dispatch(Request('POST','/api/workspace/write'+query,{'Authorization':auth},raw if raw is not None else json.dumps(body).encode()))
    def test_auth_and_missing_policy_before_io(self):
        self.assertEqual(self.call(auth='no').status,401);self.assertEqual(self.resolutions,0)
        self.router=self.make(mutation_authorize=None);self.assertEqual(self.call().status,503);self.assertEqual(list(self.root.iterdir()),[])
    def test_create_source_dto_exact_proposal_and_mode(self):
        r=self.call({'path':'é.txt','content':'café'})
        self.assertEqual(r.status,200);self.assertEqual(r.json(),{'ok':True,'path':'é.txt'})
        self.assertEqual((self.root/'é.txt').read_text(),'café');self.assertEqual(stat.S_IMODE((self.root/'é.txt').stat().st_mode),0o600)
        p=self.proposals[0];self.assertEqual(p.content_bytes,5);self.assertEqual(p.content_sha256,hashlib.sha256('café'.encode()).hexdigest());self.assertEqual(p.task_id,5);self.assertEqual(p.revision,'rev')
        self.assertGreaterEqual(len(self.proposals),4)
    def test_replace_preserves_rwx_strips_special_bits(self):
        f=self.root/'x';f.write_text('old');f.chmod(0o4750)
        self.assertEqual(self.call().status,200);self.assertEqual(f.read_text(),'hello');self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o750)
    def test_normalization_and_unknown_duplicate_fields_rejected(self):
        for path in ('../outside','/etc/x','a/../x','a//x','a\\x','x\nx'):
            self.assertEqual(self.call({'path':path,'content':'x'}).status,400)
        self.assertEqual(self.call({'path':'x','content':'x','task':7}).status,400)
        self.assertEqual(self.call(raw=b'{"path":"x","path":"y","content":"x"}').status,400)
        self.assertEqual(self.call(query='?task=5').status,400);self.assertEqual(list(self.root.iterdir()),[])
    def test_binary_invalid_unicode_and_byte_limit_rejected(self):
        self.assertEqual(self.call({'path':'x','content':'\0'}).status,400)
        self.assertEqual(self.call({'path':'x','content':'\ud800'}).status,400)
        self.router=self.make(max_bytes=4)
        self.assertEqual(self.call({'path':'x','content':'ééé'}).status,413);self.assertEqual(list(self.root.iterdir()),[])
    def test_denied_and_stale_preparation_no_mutation(self):
        self.allow=False;self.assertEqual(self.call().status,403)
        self.allow=True;self.valid=False;self.assertEqual(self.call().status,403);self.assertEqual(list(self.root.iterdir()),[])
    def test_no_mkdir_symlink_hardlink_or_special_write(self):
        r=self.call({'path':'new/x','content':'x'});self.assertEqual(r.json()['outcome'],'unknown');self.assertFalse((self.root/'new').exists())
        (self.root/'real').write_text('old');os.symlink('real',self.root/'x')
        self.assertEqual(self.call().status,500);self.assertEqual((self.root/'real').read_text(),'old')
        (self.root/'x').unlink();os.link(self.root/'real',self.root/'x')
        self.assertEqual(self.call().status,500);self.assertEqual((self.root/'real').read_text(),'old')
    def test_root_inode_pin(self):
        self.binding=WorkspaceBinding(5,str(self.root),0,0,'rev')
        self.assertEqual(self.call().status,503);self.assertEqual(list(self.root.iterdir()),[])
    def test_precommit_revocation_does_not_rename_or_retry(self):
        f=self.root/'x';f.write_text('old');calls=[]
        def policy(req,b,p):
            calls.append(p)
            return len(calls)<4
        self.router=self.make(mutation_authorize=policy)
        r=self.call();self.assertEqual(r.status,500);self.assertFalse(r.json()['retry']);self.assertEqual(f.read_text(),'old')
        self.assertEqual([p.name for p in self.root.iterdir()],['x'])
    def test_directory_fsync_failure_after_rename_uncertain(self):
        real=os.fsync;calls=[]
        def fsync(fd):
            calls.append(fd)
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('private path should not leak')
            return real(fd)
        with patch('artex.tools.filesystem.os.fsync',fsync):r=self.call()
        self.assertEqual(r.status,500);self.assertEqual((self.root/'x').read_text(),'hello')
        self.assertEqual(r.json()['outcome'],'unknown');self.assertNotIn('private',r.body.decode());self.assertEqual(len(calls),2)
    def test_postrename_scope_change_no_false_success(self):
        real=os.replace;calls=[]
        def replace(*a,**kw):
            calls.append(a);real(*a,**kw);self.valid=False
        with patch('artex.tools.filesystem.os.replace',replace):r=self.call()
        self.assertEqual(r.status,500);self.assertEqual((self.root/'x').read_text(),'hello');self.assertEqual(len(calls),1)
