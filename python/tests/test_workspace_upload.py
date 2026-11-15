"""Multipart upload tests write only new temporary synthetic files."""
import json,os,stat,tempfile,unittest
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch
from artex.server.workspace_upload import WorkspaceUploadService
from artex.server.workspace import WorkspaceBinding
from artex.server.core import Request,Router

def body(name='x.bin',data=b'\0\xff',extra=b'',headers=None):
    head=headers if headers is not None else ('Content-Disposition: form-data; name="file"; filename="'+name+'"\r\nContent-Type: application/octet-stream').encode()
    return b'--fixture\r\n'+head+b'\r\n\r\n'+data+extra+b'\r\n--fixture--\r\n'
class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);s=self.root.stat();self.binding=WorkspaceBinding(5,str(self.root),s.st_dev,s.st_ino,'r')
        self.allowed=True;self.valid=True;self.proposals=[];self.resolves=0;self.router=self.make()
    def make(self,**kw):
        def resolve(req):self.resolves+=1;return self.binding
        def allow(req,p):self.proposals.append(p);return self.allowed
        args=dict(resolve=resolve,authorize=lambda *a:True,current=lambda b:self.valid,writer_guard=lambda b:nullcontext(),upload_authorize=allow);args.update(kw)
        self.service=WorkspaceUploadService(**args);r=Router(lambda req:req.header('Authorization')=='yes');self.service.register(r);return r
    def call(self,data=None,query='',auth='yes',content_type='multipart/form-data; boundary=fixture'):
        return self.router.dispatch(Request('POST','/api/workspace/upload'+query,{'Authorization':auth,'Content-Type':content_type},body() if data is None else data))
    def test_auth_and_explicit_action_grant(self):
        self.assertEqual(self.call(auth='no').status,401);self.assertEqual(self.resolves,0)
        self.allowed=False;self.assertEqual(self.call().status,403);self.assertEqual(list(self.root.iterdir()),[])
        self.router=self.make(upload_authorize=None);self.assertEqual(self.call().status,503)
    def test_binary_exact_bytes_success_proposal_mode(self):
        r=self.call();self.assertEqual(r.json(),{'uploaded':1});f=self.root/'x.bin';self.assertEqual(f.read_bytes(),b'\0\xff');self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o600)
        p=self.proposals[0];self.assertEqual(p.size,2);self.assertEqual(p.path,'x.bin');self.assertIsNone(p.previous_version);self.assertEqual(len(p.sha256),64)
    def test_overwrite_carries_old_version_and_keeps_mode(self):
        f=self.root/'x.bin';f.write_bytes(b'old');f.chmod(0o640)
        self.assertEqual(self.call().status,200);self.assertIsNotNone(self.proposals[0].previous_version);self.assertEqual(stat.S_IMODE(f.stat().st_mode),0o640)
    def test_nested_existing_dir_no_creation(self):
        self.assertEqual(self.call(query='?path=missing').status,404);self.assertFalse((self.root/'missing').exists())
        (self.root/'d').mkdir();self.assertEqual(self.call(query='?path=d').status,200);self.assertEqual((self.root/'d'/'x.bin').read_bytes(),b'\0\xff')
    def test_unsafe_names_queries_and_duplicate_metadata_rejected(self):
        for name in ('../x','a/b','a\\b','.','..','x\nx'):
            self.assertEqual(self.call(body(name)).status,400)
        self.assertEqual(self.call(query='?path=d&path=e').status,400)
        bad=b'Content-Disposition: form-data; name="file"; filename="x"; filename="y"'
        self.assertEqual(self.call(body(headers=bad)).status,400);self.assertEqual(list(self.root.iterdir()),[])
    def test_multiple_files_nested_encoded_malformed_rejected_before_writes(self):
        samples=[body(extra=b'\r\n--fixture\r\nContent-Disposition: form-data; name="file"; filename="y"\r\n\r\nx'),body(headers=b'Content-Disposition: form-data; name="file"; filename*=UTF-8\'\'x'),body(headers=b'Content-Disposition: form-data; name="file"; filename="x"\r\nContent-Transfer-Encoding: base64'),body(headers=b'Content-Disposition: form-data; name="file"; filename="x"\r\nContent-Type: multipart/mixed'),b'preamble'+body(),body()+b'epilogue',body()[:-4]]
        for sample in samples:self.assertEqual(self.call(sample).status,400)
        self.assertEqual(list(self.root.iterdir()),[])
    def test_symlink_hardlink_parent_refusal(self):
        (self.root/'real').write_bytes(b'old');os.symlink('real',self.root/'x.bin');self.assertEqual(self.call().status,400)
        (self.root/'x.bin').unlink();os.link(self.root/'real',self.root/'x.bin');self.assertEqual(self.call().status,400)
        (self.root/'dir').mkdir();os.symlink('dir',self.root/'alias');self.assertEqual(self.call(query='?path=alias').status,400)
        self.assertEqual((self.root/'real').read_bytes(),b'old')
    def test_last_policy_callback_version_change_prevents_overwrite(self):
        f=self.root/'x.bin';f.write_bytes(b'old');calls=[]
        def policy(req,p):
            calls.append(1)
            if len(calls)==3:f.write_bytes(b'changed')
            return True
        self.router=self.make(upload_authorize=policy);r=self.call();self.assertEqual(r.status,500);self.assertEqual(r.json()['outcome'],'unknown');self.assertEqual(f.read_bytes(),b'changed')
    def test_postrename_fsync_failure_unknown_no_retry(self):
        real=os.fsync
        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('private failure')
            return real(fd)
        with patch('artex.tools.filesystem.os.fsync',fsync):r=self.call()
        self.assertEqual(r.status,500);self.assertFalse(r.json()['retry']);self.assertEqual((self.root/'x.bin').read_bytes(),b'\0\xff');self.assertNotIn(b'private',r.body)
    def test_body_limit_before_resolution(self):
        self.router=self.make(max_bytes=1)
        self.assertEqual(self.call(b'x'*16386).status,413);self.assertEqual(self.resolves,0)
