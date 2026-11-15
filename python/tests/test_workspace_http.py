"""Temporary fixtures only; no user files or listener."""
from contextlib import nullcontext
from pathlib import Path
import os,tempfile,unittest
from urllib.parse import quote
from artex.server.workspace import WorkspaceService,WorkspaceBinding
from artex.server.core import Router,Request
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        st=self.root.stat();self.binding=WorkspaceBinding(5,str(self.root),st.st_dev,st.st_ino,'r');self.valid=True;self.allowed=True;self.resolutions=0
        def resolve(req):self.resolutions+=1;return self.binding
        self.service=WorkspaceService(resolve=resolve,authorize=lambda *a:self.allowed,current=lambda b:self.valid,lifecycle_guard=lambda b:nullcontext())
        self.router=Router(lambda req:req.header('Authorization')=='yes');self.service.register(self.router)
    def call(self,action='list',path='',extra='',headers=None,method='GET'):
        return self.router.dispatch(Request(method,'/api/workspace/'+action+'?path='+quote(path,safe='')+extra,headers or {'Authorization':'yes'}))
    def test_auth_before_resolver(self):
        self.assertEqual(self.call(headers={'Authorization':'no'}).status,401);self.assertEqual(self.resolutions,0)
    def test_list_exact_shape_sort(self):
        (self.root/'b').write_text('hi');(self.root/'A').mkdir()
        r=self.call();self.assertEqual(r.status,200);self.assertEqual([e['name'] for e in r.json()['entries']],['A','b']);self.assertEqual(r.json()['path'],'')
        self.assertEqual(set(r.json()['entries'][0]),{'name','path','dir','size','mtime'})
    def test_text_binary_and_oversized(self):
        (self.root/'x').write_text('hello');self.assertEqual(self.call('read','x').json()['content'],'hello')
        (self.root/'x').write_bytes(b'\xff');self.assertEqual(self.call('read','x').json(),{'path':'x','size':1,'binary':True})
        (self.root/'x').write_bytes(b'x'*(2*1024*1024+1));self.assertTrue(self.call('read','x').json()['too_large'])
    def test_download_unicode_safe_headers(self):
        name='é";.txt';(self.root/name).write_bytes(b'\x00x')
        r=self.call('download',name);self.assertEqual(r.body,b'\x00x');self.assertEqual(r.status,200)
        self.assertIn("filename*=UTF-8''%C3%A9%22%3B.txt",r.headers['Content-Disposition']);self.assertNotIn('\n',r.headers['Content-Disposition'])
    def test_traversal_absolute_controls_duplicate_rejected(self):
        for path in ('../x','/etc/passwd','a/../x','x\\y','a\nx'):
            self.assertEqual(self.call('read',path).status,400)
        self.assertEqual(self.call(extra='&path=x').status,400);self.assertEqual(self.call(extra='&task=6').status,400)
    def test_symlink_and_hardlink_not_read(self):
        (self.root/'real').write_text('private');os.symlink('real',self.root/'link');os.link(self.root/'real',self.root/'hard')
        for name in ('link','hard','real'):self.assertEqual(self.call('read',name).status,400)
        self.assertEqual(self.call().status,400)
    def test_root_symlink_and_inode_rebinding_fail(self):
        self.binding=WorkspaceBinding(5,str(self.root),0,0,'r');self.assertEqual(self.call().status,503)
    def test_denied_or_stale_no_content(self):
        (self.root/'x').write_text('secret');self.allowed=False;self.assertEqual(self.call('read','x').status,403)
        self.allowed=True;self.valid=False;self.assertEqual(self.call('download','x').status,403)
    def test_no_write_routes_range_or_unbounded_download(self):
        self.assertEqual(self.call('write',method='POST').status,404)
        (self.root/'x').write_bytes(b'x'*(8*1024*1024+1));self.assertEqual(self.call('download','x').status,413)
        self.assertEqual(self.call('download','x',headers={'Authorization':'yes','Range':'bytes=0-1'}).status,400)
    def test_entry_bound_no_silent_truncation(self):
        self.service.max_entries=1
        (self.root/'a').write_text('a');(self.root/'b').write_text('b');self.assertEqual(self.call().status,413)
