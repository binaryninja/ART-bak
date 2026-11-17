import unittest
from unittest.mock import patch
import test_skill_read_setup as base
import test_skill_content_read as content
from artex.server.skill_read_setup import bind_skill_reads
from artex.server.skill_content_setup import _SkillContentApplication

class Tests(unittest.TestCase):
    def setUp(self):
        self.b=base.Tests();self.b.setUp();self.addCleanup(self.b.doCleanups)
        self.c=content.Tests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.allowed=[True]*4;self.calls=[0]*4;self.hooks=[lambda:None]*4
        self.bundle=self.bind()
    def bind(self,**overrides):
        def grant(i):
            def allow(*a):self.calls[i]+=1;self.hooks[i]();return self.allowed[i]
            return allow
        args=dict(usage_service=self.b.u.service,missing_service=self.b.m.service,
                  files_service=self.b.f.service,content_service=self.c.service,
                  authorize_usage=grant(0),authorize_missing=grant(1),authorize_files=grant(2),authorize_content=grant(3))
        args.update(overrides)
        return bind_skill_reads(self.b.t.original,**args)
    def request(self,i=3,path=None,**kw):
        paths=('/api/skills/sample/usage','/api/skills/missing','/api/skills/sample/files','/api/skills/sample/files/SKILL.md')
        return self.b.t.request(path or paths[i],application=self.bundle.application,**kw)
    def test_inert_four_families_and_nested(self):
        self.assertEqual(self.calls,[0]*4)
        for i in range(4):self.assertEqual(self.request(i)[0],200)
        (self.c.f.f.skill/'refs').mkdir();(self.c.f.f.skill/'refs'/'a').write_text('nested')
        # PATH_INFO is already URI-decoded; a remaining percent must not be decoded again.
        self.assertEqual(self.request(path='/api/skills/sample/files/refs%2Fa')[0],400)
        self.assertEqual(self.request(path='/api/skills/sample/files/refs/a')[0],200)
        self.assertEqual(self.request(method='POST')[0],405)
        self.assertEqual(self.request(method='HEAD')[0],200)
    def test_optional_pair_and_omission(self):
        for values in ({'content_service':None},{'authorize_content':None}):
            with self.assertRaises(ValueError):self.bind(**values)
        self.bundle=self.bind(content_service=None,authorize_content=None)
        self.assertIsNone(self.bundle.content)
        self.assertEqual(self.request()[0],404)
        self.assertEqual(self.request(2)[0],200)
        self.assertEqual(self.calls[3],0)
    def test_scopes_do_not_bleed(self):
        for selected in range(4):
            self.allowed=[i==selected for i in range(4)];self.calls=[0]*4
            self.assertEqual(self.request(selected)[0],200)
            self.assertTrue(all(v==0 for i,v in enumerate(self.calls) if i!=selected))
            for denied in range(4):
                if denied!=selected:self.assertEqual(self.request(denied)[0],403)
    def test_no_jwt_no_io(self):
        with patch('artex.skills.file_read.ScopedFilesystem') as fs:
            self.assertEqual(self.request(token=False)[0],401);fs.assert_not_called()
        self.assertEqual(self.calls,[0]*4)
    def test_late_conflict_atomic_all_methods(self):
        self.b.t.original.router.add('PUT','/api/skills/{name}/files/refs/{file}',lambda *a:None)
        before=tuple(self.b.t.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(before,tuple(self.b.t.original.router._routes));self.assertEqual(self.calls,[0]*4)
    def test_prebind_content_methods_and_binder(self):
        for name in ('__init__','__call__','_check'):
            with patch.object(_SkillContentApplication,name,lambda *a:None):
                with self.assertRaises(ValueError):self.bind()
        with patch('artex.server.skill_read_setup.bind_skill_content_reads',lambda *a,**k:None):
            with self.assertRaises(ValueError):self.bind()
        self.assertEqual(self.calls,[0]*4)
    def test_selected_view_swap(self):
        app=self.bundle.application;old=app.content_application
        app.content_application=lambda *a:[]
        self.assertEqual(self.request()[0],503);app.content_application=old
        with self.assertRaisesRegex(RuntimeError,'composition changed'):
            self.request(start=lambda:setattr(app,'content_application',lambda *a:[]))
    def test_postheaders_receipt_change(self):
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
            self.request(start=lambda:self.c.f.path.write_text('replaced'))
    def test_final_outer_expiry(self):
        now=[1000.];self.b.t.auth.clock=lambda:now[0];self.bundle=self.bind()
        self.assertEqual(self.request()[0],200);final=self.calls[3];self.calls=[0]*4
        def expire():
            if self.calls[3]==final:now[0]=1000000.
        self.hooks[3]=expire
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_postheaders_grants(self):
        for i in range(4):
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                self.request(i,start=lambda:self.allowed.__setitem__(i,False))
            self.allowed[i]=True
    def test_unrelated_retirement(self):
        self.c.reader.current=lambda *a:False
        self.assertEqual(self.request(2)[0],200);self.assertEqual(self.request()[0],503)

if __name__=='__main__':unittest.main()
