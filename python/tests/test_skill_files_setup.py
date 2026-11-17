import unittest
from unittest.mock import patch
import test_intercept_read_setup as transport
import test_skill_files_read as source
from artex.server.skill_files_setup import bind_skill_files_reads

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.u=source.Tests();self.u.setUp();self.addCleanup(self.u.doCleanups)
        self.allowed=True;self.names=[];self.grant_hook=lambda:None;self.bundle=self.bind()
    def bind(self):
        def grant(r,s,n):self.names.append(n);self.grant_hook();return self.allowed
        return bind_skill_files_reads(self.t.original,files_service=self.u.service,authorize_global=grant)
    def request(self,**kwargs):return self.t.request('/api/skills/sample/files',application=self.bundle.application,**kwargs)
    def test_optional_jwt_before_filesystem(self):
        self.assertEqual(self.t.request('/api/skills/sample/files',application=self.t.original)[0],404)
        self.assertEqual(self.names,[])
        with patch('artex.skills.file_listing.ScopedFilesystem') as fs:
            self.assertEqual(self.request(token=False)[0],401);fs.assert_not_called()
        status,body=self.request();self.assertEqual(status,200);self.assertIn(b'secret-name',body)
    def test_methods_and_unrelated_route(self):
        for method in ('POST','PUT','DELETE'):self.assertEqual(self.request(method=method)[0],405)
        self.assertEqual(self.request(method='HEAD'),(200,b''))
        self.assertEqual(self.t.request('/api/health',token=False,application=self.bundle.application)[0],200)
    def test_atomic_conflicts(self):
        for path in ('/api/skills/sample/files','/api/skills/{s}/files','/a{x}/skills/sample/f{y}'):
            t=transport.Tests();t.setUp();self.addCleanup(t.doCleanups)
            t.original.router.add('HEAD',path,lambda r,p:None);before=tuple(t.original.router._routes)
            with self.assertRaises(ValueError):bind_skill_files_reads(t.original,files_service=self.u.service,authorize_global=lambda *a:True)
            self.assertEqual(tuple(t.original.router._routes),before)
    def test_all_grants_before_access(self):
        for owner,name in ((self,'allowed'),(self.u,'allowed'),(self.u.f,'allowed'),(self.u.f,'current')):
            setattr(owner,name,False)
            with patch('artex.skills.file_listing.ScopedFilesystem') as fs:
                self.assertEqual(self.request()[0],403);fs.assert_not_called()
            setattr(owner,name,True)
    def test_post_header_revocation(self):
        for owner,name in ((self,'allowed'),(self.u,'allowed'),(self.u.f,'allowed'),(self.u.f,'current')):
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                self.request(start=lambda:setattr(owner,name,False))
            setattr(owner,name,True)
    def test_post_header_root_replacement(self):
        root=self.u.f.root;moved=root.with_name(root.name+'-old')
        self.addCleanup(lambda:__import__('shutil').rmtree(moved,ignore_errors=True))
        def replace():root.rename(moved);root.mkdir()
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request(start=replace)
    def test_original_auth_replacement_denied(self):
        self.u.f.hook=lambda:setattr(self.t.original.router,'authorize',lambda r:True)
        status,body=self.request();self.assertNotEqual(status,200);self.assertNotIn(b'secret-name',body)
    def test_source_methods_pinned(self):
        self.u.reader._root_current=lambda:None
        with self.assertRaises(ValueError):self.bind()
    def test_errors_no_paths(self):
        for path in ('/api/skills/absent/files','/api/skills/INVALID/files'):
            status,body=self.t.request(path,application=self.bundle.application)
            self.assertIn(status,(400,404));self.assertNotIn(str(self.u.f.root).encode(),body)
    def test_final_outer_grant_root_swap(self):
        self.assertEqual(self.request()[0],200);final=len(self.names);self.names.clear()
        root=self.u.f.root;moved=root.with_name(root.name+'-old')
        self.addCleanup(lambda:__import__('shutil').rmtree(moved,ignore_errors=True))
        def replace():
            if len(self.names)==final:root.rename(moved);root.mkdir()
        self.grant_hook=replace
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_final_current_revokes_outer_request_source_or_jwt(self):
        now=[1000.];self.t.auth.clock=lambda:now[0];self.bundle=self.bind()
        calls=[0]
        def count():calls[0]+=1
        self.u.f.hook=count;self.assertEqual(self.request()[0],200);final=calls[0]
        for target in ('outer','request','source','jwt'):
            calls[0]=0
            def revoke():
                calls[0]+=1
                if calls[0]==final:
                    if target=='outer':self.allowed=False
                    elif target=='request':self.u.allowed=False
                    elif target=='source':self.u.f.allowed=False
                    else:now[0]=1000000.
            self.u.f.hook=revoke
            with self.subTest(target=target),self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
            self.allowed=self.u.allowed=self.u.f.allowed=True;now[0]=1000.
    def test_final_outer_callback_expires_jwt(self):
        now=[1000.];self.t.auth.clock=lambda:now[0];self.bundle=self.bind()
        self.assertEqual(self.request()[0],200);final=len(self.names);self.names.clear()
        def expire():
            if len(self.names)==final:now[0]=1000000.
        self.grant_hook=expire
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()

if __name__=='__main__':unittest.main()
