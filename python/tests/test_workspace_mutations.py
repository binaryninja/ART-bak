"""New temporary synthetic trees only; no user data or recursive deletion."""
from contextlib import nullcontext
from pathlib import Path
import json,os,stat,tempfile,unittest
from urllib.parse import quote
from unittest.mock import patch
from artex.server.core import Request,Router
from artex.server.workspace import WorkspaceBinding
from artex.server.workspace_mutations import WorkspaceMutationService
class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);s=self.root.stat()
        self.binding=WorkspaceBinding(5,str(self.root),s.st_dev,s.st_ino,'r');self.valid=True;self.allowed=True;self.proposals=[];self.resolutions=[];self.router=self.make()
    def make(self,**kw):
        def resolve(req):self.resolutions.append(1);return self.binding
        def grant(req,p):self.proposals.append(p);return self.allowed
        opts=dict(resolve=resolve,authorize=lambda *a:True,current=lambda b:self.valid,writer_guard=lambda b:nullcontext(),mutation_authorize=grant,delete_authorize=lambda *a:True);opts.update(kw)
        self.service=WorkspaceMutationService(**opts);r=Router(lambda req:req.header('Authorization')=='yes');self.service.register(r);return r
    def call(self,action='mkdir',path='new',auth='yes',raw=None,extra=''):
        target='/api/workspace/'+action
        body=json.dumps({'path':path}).encode() if action=='mkdir' else b''
        if action=='delete':target+='?path='+quote(path,safe='')+extra
        return self.router.dispatch(Request('POST' if action=='mkdir' else 'DELETE',target,{'Authorization':auth},body if raw is None else raw))
    def test_auth_and_missing_mutation_policy_no_effect(self):
        self.assertEqual(self.call(auth='no').status,401);self.assertEqual(self.resolutions,[])
        self.router=self.make(mutation_authorize=None);self.assertEqual(self.call().status,503);self.assertFalse((self.root/'new').exists())
    def test_mkdir_existing_parent_source_success_and_mode(self):
        r=self.call();self.assertEqual(r.json(),{'ok':True,'path':'new'});self.assertEqual(stat.S_IMODE((self.root/'new').stat().st_mode),0o700)
        self.assertEqual(self.call().status,200);self.assertEqual(self.proposals[0].target.kind,'missing')
    def test_no_recursive_parent_creation(self):
        self.assertEqual(self.call(path='one/two').status,404);self.assertFalse((self.root/'one').exists())
    def test_delete_file_and_empty_directory(self):
        (self.root/'x').write_text('fixture');self.assertEqual(self.call('delete','x').json(),{'ok':True});self.assertFalse((self.root/'x').exists())
        (self.root/'d').mkdir();self.assertEqual(self.call('delete','d').status,200)
        self.assertEqual(self.call('delete','d').status,404)
    def test_nonempty_directory_no_partial_removal(self):
        (self.root/'d').mkdir();(self.root/'d'/'x').write_text('fixture')
        self.assertEqual(self.call('delete','d').status,409);self.assertEqual((self.root/'d'/'x').read_text(),'fixture')
    def test_explicit_delete_grant_and_proposal_identity(self):
        (self.root/'x').write_text('fixture');self.router=self.make(delete_authorize=lambda *a:False)
        self.assertEqual(self.call('delete','x').status,403);self.assertTrue((self.root/'x').exists())
        p=self.proposals[-1];self.assertEqual(p.target.kind,'file');self.assertEqual(p.target.target_fingerprint[1],(self.root/'x').stat().st_ino);self.assertEqual(p.binding.task_id,5)
        self.router=self.make(delete_authorize=None);self.assertEqual(self.call('delete','x').status,503)
    def test_root_traversal_symlink_and_hardlink_reject(self):
        for path in ('','.','..','../other','/tmp/x','a/../x','a\\b'):
            self.assertEqual(self.call('delete',path).status,400)
        (self.root/'real').write_text('fixture');os.symlink('real',self.root/'link');os.link(self.root/'real',self.root/'hard')
        for path in ('link','hard','real'):self.assertEqual(self.call('delete',path).status,400)
        self.assertTrue((self.root/'real').exists())
    def test_target_replaced_during_policy_not_deleted(self):
        f=self.root/'x';f.write_text('old');called=[]
        def grant(req,p):
            if not called:called.append(1);f.unlink();f.write_text('replacement')
            return True
        self.router=self.make(mutation_authorize=grant)
        self.assertEqual(self.call('delete','x').status,409);self.assertEqual(f.read_text(),'replacement')
    def test_fsync_failure_after_delete_reports_unknown_no_retry(self):
        f=self.root/'x';f.write_text('fixture')
        with patch('artex.tools.workspace_mutations.os.fsync',side_effect=OSError('private fixture')):r=self.call('delete','x')
        self.assertEqual(r.status,500);self.assertEqual(r.json()['outcome'],'unknown');self.assertFalse(r.json()['retry']);self.assertFalse(f.exists());self.assertNotIn(b'private',r.body)
    def test_postmkdir_revocation_reports_effect_not_rollback(self):
        real=os.mkdir
        def mkdir(*a,**kw):real(*a,**kw);self.valid=False
        with patch('artex.tools.workspace_mutations.os.mkdir',mkdir):r=self.call()
        self.assertEqual(r.status,500);self.assertEqual(r.json()['outcome'],'unknown');self.assertTrue((self.root/'new').is_dir())
    def test_duplicate_query_and_json_no_effect(self):
        self.assertEqual(self.call('delete','x',extra='&path=y').status,400)
        self.assertEqual(self.call(raw=b'{"path":"a","path":"b"}').status,400)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_symlink_ancestor_invalid_path_no_mutation(self):
        (self.root/'real').mkdir();(self.root/'real'/'x').write_text('fixture');os.symlink('real',self.root/'alias')
        for action,path in (('delete','alias/x'),('mkdir','alias/new')):
            r=self.call(action,path);self.assertEqual(r.status,400);self.assertNotIn(b'alias',r.body)
        self.assertEqual((self.root/'real'/'x').read_text(),'fixture');self.assertFalse((self.root/'real'/'new').exists())
