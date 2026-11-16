import unittest
from unittest.mock import patch
import test_intercept_read_setup as transport
import test_skill_content_read as source
from artex.server.skill_content_setup import bind_skill_content_reads

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.u=source.Tests();self.u.setUp();self.addCleanup(self.u.doCleanups)
        self.allowed=True;self.calls=0;self.hook=lambda:None;self.bundle=self.bind()
    def bind(self):
        def grant(*a):self.calls+=1;self.hook();return self.allowed
        return bind_skill_content_reads(self.t.original,content_service=self.u.service,authorize_global=grant)
    def request(self,path='/api/skills/sample/files/SKILL.md',**kwargs):return self.t.request(path,application=self.bundle.application,**kwargs)
    def test_optin_jwt_before_read_and_nested_success(self):
        self.assertEqual(self.calls,0)
        self.assertEqual(self.t.request('/api/skills/sample/files/SKILL.md',application=self.t.original)[0],404)
        with patch('artex.skills.file_read.ScopedFilesystem') as fs:
            self.assertEqual(self.request(token=False)[0],401);fs.assert_not_called()
        self.assertEqual(self.request()[0],200)
        (self.u.f.f.skill/'refs').mkdir();(self.u.f.f.skill/'refs'/'a').write_text('nested')
        self.assertEqual(self.request('/api/skills/sample/files/refs/a')[0],200)
    def test_methods_and_other_routes(self):
        for method in ('POST','PUT','DELETE'):self.assertEqual(self.request(method=method)[0],405)
        self.assertEqual(self.request(method='HEAD'),(400,b''))
        self.assertEqual(self.request('/api/health',token=False)[0],200)
    def test_atomic_route_conflicts_all_methods(self):
        for path in ('/api/skills/sample/files/a','/api/skills/{x}/files/a/b','/a{x}/skills/{n}/f{y}/{z}'):
            t=transport.Tests();t.setUp();self.addCleanup(t.doCleanups)
            t.original.router.add('PUT',path,lambda *a:None);before=tuple(t.original.router._routes)
            with self.assertRaises(ValueError):bind_skill_content_reads(t.original,content_service=self.u.service,authorize_global=lambda *a:True)
            self.assertEqual(tuple(t.original.router._routes),before)
    def test_each_grant_before_fs(self):
        for owner,name in ((self,'allowed'),(self.u,'allowed'),(self.u.f,'allowed'),(self.u.f,'current')):
            setattr(owner,name,False)
            with patch('artex.skills.file_read.ScopedFilesystem') as fs:
                self.assertEqual(self.request()[0],403);fs.assert_not_called()
            setattr(owner,name,True)
    def test_postheader_grants(self):
        for owner,name in ((self,'allowed'),(self.u,'allowed'),(self.u.f,'allowed'),(self.u.f,'current')):
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request(start=lambda:setattr(owner,name,False))
            setattr(owner,name,True)
    def test_postheader_file_write(self):
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
            self.request(start=lambda:self.u.f.path.write_text('changed after headers'))
    def test_last_outer_file_write(self):
        self.assertEqual(self.request()[0],200);final=self.calls;self.calls=0
        self.hook=lambda:self.u.f.path.write_text('changed at final outer') if self.calls==final else None
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_last_outer_jwt_expiry(self):
        now=[1000.];self.t.auth.clock=lambda:now[0];self.bundle=self.bind()
        self.assertEqual(self.request()[0],200);final=self.calls;self.calls=0
        def expire():
            if self.calls==final:now[0]=1000000.
        self.hook=expire
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_replaced_receipt_validation_refused(self):
        self.u.reader._receipt_check=lambda *a:None
        with self.assertRaises(ValueError):self.bind()
    def test_errors_sanitized(self):
        status,body=self.request('/api/skills/sample/files/absent')
        self.assertEqual(status,404);self.assertNotIn(str(self.u.f.f.root).encode(),body)
    def test_prebind_class_method_replacement_refused(self):
        from artex.server.skill_content_setup import _SkillContentApplication
        from artex.server.skill_content_read import SkillContentReadService
        from artex.skills.file_read import SkillFileReader
        for cls,method in ((_SkillContentApplication,'__call__'),(SkillContentReadService,'_prepare'),(SkillFileReader,'_receipt_check')):
            with self.subTest(cls=cls,method=method),patch.object(cls,method,lambda *a,**k:None):
                with self.assertRaises(ValueError):self.bind()
    def test_final_current_revokes_grants_and_jwt(self):
        now=[1000.];self.t.auth.clock=lambda:now[0];self.bundle=self.bind();calls=[0]
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

if __name__=='__main__':unittest.main()
