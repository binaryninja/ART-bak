import unittest
from unittest.mock import patch
import test_intercept_read_setup as transport
import test_skill_usage_read as usage
import test_missing_skill_read as missing
import test_skill_files_read as files
from artex.server.skill_read_setup import bind_skill_reads

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.u=usage.Tests();self.u.setUp();self.addCleanup(self.u.doCleanups)
        self.m=missing.Tests();self.m.setUp();self.addCleanup(self.m.doCleanups)
        self.f=files.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=[True]*3;self.calls=[0]*3;self.hooks=[lambda:None]*3
        self.bundle=self.bind()
    def bind(self):
        def grant(index):
            def allow(*args):self.calls[index]+=1;self.hooks[index]();return self.allowed[index]
            return allow
        return bind_skill_reads(self.t.original,usage_service=self.u.service,missing_service=self.m.service,
            files_service=self.f.service,authorize_usage=grant(0),authorize_missing=grant(1),authorize_files=grant(2))
    def request(self,index,**kwargs):
        path=('/api/skills/sample/usage','/api/skills/missing','/api/skills/sample/files')[index]
        return self.t.request(path,application=self.bundle.application,**kwargs)
    def test_inert_binding_all_three_reachable(self):
        self.assertEqual(self.calls,[0]*3);self.assertFalse(self.u.db.calls);self.assertFalse(self.m.db.calls)
        for i in range(3):self.assertEqual(self.request(i)[0],200)
        self.assertEqual(len(self.u.db.calls),1);self.assertEqual(len(self.m.db.calls),1)
        self.assertTrue(all(n>0 for n in self.calls))
    def test_selected_permissions_do_not_bleed(self):
        for selected in range(3):
            self.allowed=[i==selected for i in range(3)];self.calls=[0]*3
            self.assertEqual(self.request(selected)[0],200)
            self.assertTrue(all(n==0 for i,n in enumerate(self.calls) if i!=selected))
            for denied in range(3):
                if denied!=selected:self.assertEqual(self.request(denied)[0],403)
    def test_jwt_before_any_reads(self):
        with patch('artex.skills.file_listing.ScopedFilesystem') as fs:
            for i in range(3):self.assertEqual(self.request(i,token=False)[0],401)
            fs.assert_not_called()
        self.assertFalse(self.u.db.calls);self.assertFalse(self.m.db.calls);self.assertEqual(self.calls,[0]*3)
    def test_conflict_late_family_atomic(self):
        self.t.original.router.add('GET','/api/skills/{name}/files',lambda *a:None)
        before=tuple(self.t.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(tuple(self.t.original.router._routes),before);self.assertEqual(self.calls,[0]*3)
    def test_original_health_and_unknown_preserved(self):
        self.assertEqual(self.t.request('/api/health',token=False,application=self.bundle.application)[0],200)
        self.assertEqual(self.t.request('/api/unknown',application=self.bundle.application)[0],404)
    def test_unrelated_family_retirement_does_not_block_selected(self):
        self.f.reader.current=lambda *a:False
        self.assertEqual(self.request(0)[0],200);self.assertEqual(self.request(1)[0],200)
        self.assertEqual(self.request(2)[0],503)
    def test_composed_family_swap_before_and_after_headers(self):
        original=self.bundle.application.files_application
        self.bundle.application.files_application=lambda *a:[]
        self.assertEqual(self.request(2)[0],503)
        self.bundle.application.files_application=original
        with self.assertRaisesRegex(RuntimeError,'composition changed'):
            self.request(2,start=lambda:setattr(self.bundle.application,'files_application',lambda *a:[]))
    def test_late_outer_callback_jwt_expiry_all_families(self):
        now=[1000.];self.t.auth.clock=lambda:now[0];self.bundle=self.bind()
        for i in range(3):
            self.calls=[0]*3;self.assertEqual(self.request(i)[0],200);final=self.calls[i];self.calls=[0]*3
            def expire():
                if self.calls[i]==final:now[0]=1000000.
            self.hooks[i]=expire
            with self.subTest(family=i),self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request(i)
            self.hooks[i]=lambda:None;now[0]=1000.
    def test_after_headers_selected_grant_revocation(self):
        for i in range(3):
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request(i,start=lambda:self.allowed.__setitem__(i,False))
            self.allowed[i]=True
    def test_prebind_delegate_method_replacement_rejected(self):
        from artex.server.skill_usage_setup import _SkillUsageApplication
        from artex.server.missing_skill_setup import _MissingSkillApplication
        from artex.server.skill_files_setup import _SkillFilesApplication
        for cls in (_SkillUsageApplication,_MissingSkillApplication,_SkillFilesApplication):
            for method in ('__call__','_check','__init__'):
                with self.subTest(cls=cls,method=method),patch.object(cls,method,lambda *a:None):
                    with self.assertRaises(ValueError):self.bind()
        self.assertEqual(self.calls,[0]*3);self.assertFalse(self.u.db.calls);self.assertFalse(self.m.db.calls)
    def test_imported_binder_replacement_rejected(self):
        with patch('artex.server.skill_read_setup.bind_skill_files_reads',lambda *a,**k:None):
            with self.assertRaises(ValueError):self.bind()
        self.assertEqual(self.calls,[0]*3)
    def test_foreign_original_base_rejected(self):
        other=transport.Tests();other.setUp();self.addCleanup(other.doCleanups)
        application=self.bundle.application
        with self.assertRaises(ValueError):type(application)(other.original,application._views)

if __name__=='__main__':unittest.main()
