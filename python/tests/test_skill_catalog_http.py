import json,unittest
from unittest.mock import patch
import test_skill_catalog_read as source
import test_intercept_read_setup as transport
import test_skill_read_setup as composition
from artex.server.skill_catalog_read import SkillCatalogReadService
from artex.skills.catalog_read import SkillCatalogUnavailable
from artex.server.skill_catalog_setup import bind_skill_catalog_reads,_SkillCatalogApplication
from artex.server.skill_read_setup import bind_skill_reads

class Tests(unittest.TestCase):
    def setUp(self):
        self.s=source.Tests();self.s.setUp();self.addCleanup(self.s.doCleanups)
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.allowed=self.request_allowed=True;self.calls=0;self.hook=lambda:None
        self.service=SkillCatalogReadService(self.s.reader,authorize=lambda r,s:self.request_allowed)
        self.bundle=self.bind()
    def bind(self):
        def grant(*a):self.calls+=1;self.hook();return self.allowed
        return bind_skill_catalog_reads(self.t.original,catalog_service=self.service,authorize_global=grant)
    def request(self,path='/api/skills',**kw):return self.t.request(path,application=self.bundle.application,**kw)
    def test_opt_in_authenticated_metadata(self):
        self.assertEqual(self.calls,0);self.assertEqual(self.t.request('/api/skills')[0],404)
        with patch('artex.skills.catalog_read.ScopedFilesystem') as fs:
            self.assertEqual(self.request(token=False)[0],401);fs.assert_not_called()
        status,body,*_=self.request();self.assertEqual(status,200);self.assertNotIn('SECRET',str(body))
    def test_invalid_and_method_contract(self):
        self.assertEqual(self.request('/api/skills?extra=1')[0],400)
        self.assertEqual(self.request(method='POST')[0],405)
        self.assertEqual(self.request(method='HEAD')[0],400)
        self.assertEqual(self.request('/api/health',token=False)[0],200)
    def test_independent_denials(self):
        for obj,name in ((self,'allowed'),(self,'request_allowed'),(self.s,'allowed'),(self.s,'current'),(self.s.c,'allowed'),(self.s.c,'current')):
            setattr(obj,name,False)
            self.assertEqual(self.request()[0],403)
            setattr(obj,name,True)
    def test_postheader_denials(self):
        for obj,name in ((self,'allowed'),(self,'request_allowed'),(self.s,'allowed'),(self.s,'current'),(self.s.c,'allowed'),(self.s.c,'current')):
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request(start=lambda:setattr(obj,name,False))
            setattr(obj,name,True)
    def test_postheader_nested_change(self):
        (self.s.c.f.skill/'refs').mkdir()
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
            self.request(start=lambda:(self.s.c.f.skill/'refs'/'new').write_text('new'))
    def test_final_outer_expiry(self):
        now=[1000.];self.t.auth.clock=lambda:now[0];self.bundle=self.bind()
        self.assertEqual(self.request()[0],200);final=self.calls;self.calls=0
        def expire():
            if self.calls==final:now[0]=1000000.
        self.hook=expire
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_sanitized_filesystem_error(self):
        with patch('artex.skills.catalog_read.ScopedFilesystem',side_effect=OSError('PRIVATE ROOT')):
            result=self.request();self.assertEqual(result[0],503);self.assertNotIn('PRIVATE ROOT',str(result))
    def test_all_method_conflict_and_prebind(self):
        for name in ('__init__','__call__','_check'):
            with patch.object(_SkillCatalogApplication,name,lambda *a:None):
                with self.assertRaises(ValueError):self.bind()
        self.t.original.router.add('PUT','/api/skills',lambda *a:None);before=tuple(self.t.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(before,tuple(self.t.original.router._routes));self.assertEqual(self.calls,0)
    def test_all_five_composed_and_isolated(self):
        b=composition.Tests();b.setUp();self.addCleanup(b.doCleanups)
        from artex.server.skill_content_read import SkillContentReadService
        self.bundle=bind_skill_reads(self.t.original,usage_service=b.u.service,missing_service=b.m.service,files_service=b.f.service,
            authorize_usage=lambda *a:True,authorize_missing=lambda *a:True,authorize_files=lambda *a:True,
            content_service=SkillContentReadService(self.s.source,authorize=lambda *a:True),authorize_content=lambda *a:True,
            catalog_service=self.service,authorize_catalog=lambda *a:self.allowed)
        for path in ('/api/skills','/api/skills/sample/usage','/api/skills/missing','/api/skills/sample/files','/api/skills/sample/files/SKILL.md'):
            self.assertEqual(self.request(path)[0],200)
        self.allowed=False
        self.assertEqual(self.request()[0],403)
        self.assertEqual(self.request('/api/skills/sample/files/SKILL.md')[0],200)
    def test_final_catalog_current_revokes_content(self):
        calls=[0];self.s.hook=lambda:calls.__setitem__(0,calls[0]+1)
        self.assertEqual(self.request()[0],200);final=calls[0];calls[0]=0
        def revoke():
            calls[0]+=1
            if calls[0]==final:self.s.c.allowed=False
        self.s.hook=revoke
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_final_outer_revokes_content(self):
        self.assertEqual(self.request()[0],200);final=self.calls;self.calls=0
        def revoke():
            if self.calls==final:self.s.c.allowed=False
        self.hook=revoke
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_final_outer_file_change(self):
        self.assertEqual(self.request()[0],200);final=self.calls;self.calls=0
        def change():
            if self.calls==final:(self.s.c.f.skill/'new').write_text('new')
        self.hook=change
        with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
    def test_foreign_content_receiver_before_bind_and_after_headers(self):
        from contextlib import nullcontext
        from artex.skills.file_read import SkillFileReader
        foreign=SkillFileReader(self.s.c.f.scope,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        for name in ('read','validate_disclosure','_snapshot_check','_receipt_check'):
            with patch.object(self.s.source,name,getattr(foreign,name)):
                with self.assertRaises(SkillCatalogUnavailable):self.bind()
            old=getattr(self.s.source,name)
            try:
                with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                    self.request(start=lambda:setattr(self.s.source,name,getattr(foreign,name)))
            finally:setattr(self.s.source,name,old)
    def test_catalog_composition_without_content(self):
        b=composition.Tests();b.setUp();self.addCleanup(b.doCleanups)
        self.bundle=bind_skill_reads(self.t.original,usage_service=b.u.service,missing_service=b.m.service,files_service=b.f.service,
            authorize_usage=lambda *a:True,authorize_missing=lambda *a:True,authorize_files=lambda *a:True,
            catalog_service=self.service,authorize_catalog=lambda *a:True)
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.request('/api/skills/sample/files')[0],200)
        self.assertEqual(self.request('/api/skills/sample/files/SKILL.md')[0],404)
        with self.assertRaisesRegex(RuntimeError,'composition changed'):
            self.request(start=lambda:setattr(self.bundle.application,'catalog_application',None))

if __name__=='__main__':unittest.main()
