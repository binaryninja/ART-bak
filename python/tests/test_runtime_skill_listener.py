"""Owned runtime/skill listener composition, no socket in these unit tests."""
import io,json,unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch
import test_runtime_listener as runtime
import test_skill_catalog_read as catalog
from artex.db.skill_usage_read import SkillUsageReader,UsageScope
from artex.db.missing_skill_stats import MissingSkillStatsReader
from artex.db.successful_skill_stats import SuccessfulSkillStatsReader
from artex.skills.file_listing import SkillFileListingReader
from artex.skills.catalog_read import SkillCatalogReader
from artex.server.skill_usage_read import SkillUsageReadService
from artex.server.missing_skill_read import MissingSkillReadService
from artex.server.skill_files_read import SkillFilesReadService
from artex.server.skill_content_read import SkillContentReadService
from artex.server.skill_catalog_read import SkillCatalogReadService
from artex.server.skill_read_setup import bind_skill_reads,_SkillReadApplication
from artex.server.runtime_listener import RuntimeListener
from artex.server.auth import sign_jwt

class Tests(unittest.TestCase):
    def setUp(self):
        self.r=runtime.Tests();self.r.setUp();self.addCleanup(self.r.doCleanups)
        self.c=catalog.Tests();self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.allowed=True;self.hook=lambda:None;self.calls=0
        self.skills=self.build();self.listener=RuntimeListener(self.r.bundle,self.r.owner,skill_reads=self.skills)
    def build(self,base=None,pool=None):
        pool=pool or self.r.pool;scope=UsageScope('workspace','revision')
        args=dict(authorize_global=lambda *a:self.allowed,current=lambda *a:True,lifecycle_guard=nullcontext)
        usage=SkillUsageReadService(SkillUsageReader(pool,scope,**args),authorize=lambda *a:True)
        missing=MissingSkillReadService(MissingSkillStatsReader(pool,scope,**args),authorize=lambda *a:True)
        ledger=SuccessfulSkillStatsReader(pool,scope,**args)
        fs=self.c.c.f.scope
        files=SkillFilesReadService(SkillFileListingReader(fs,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext()),authorize=lambda *a:True)
        content=SkillContentReadService(self.c.source,authorize=lambda *a:True)
        cat=SkillCatalogReader(fs,self.c.source,authorize_global=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),usage_reader=ledger)
        catservice=SkillCatalogReadService(cat,authorize=lambda *a:True)
        def grant(*a):self.calls+=1;self.hook();return self.allowed
        return bind_skill_reads(base or self.r.bundle.bootstrap.app,usage_service=usage,missing_service=missing,files_service=files,
            content_service=content,catalog_service=catservice,authorize_usage=grant,authorize_missing=grant,authorize_files=grant,authorize_content=grant,authorize_catalog=grant)
    def request(self,path='/api/skills',*,auth=True,start=None,listener=None):
        statuses=[];env={'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(b'')}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        def response(status,headers):
            statuses.append(status)
            if start:start()
        body=b''.join((listener or self.listener)._view(env,response))
        return int(statuses[0].split()[0]),body
    def test_construction_inert_same_pool_and_five_routes(self):
        self.assertEqual(self.calls,0);self.assertIsNone(self.r.bundle.pump._thread)
        with patch.object(self.r.pool,'fetch_all',return_value=[]):
            for path in ('/api/skills','/api/skills/sample/usage','/api/skills/missing','/api/skills/sample/files','/api/skills/sample/files/SKILL.md'):
                self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.r.f.p.calls,[])
    def test_unauthenticated_no_sql_or_filesystem(self):
        with patch.object(self.r.pool,'fetch_all') as sql,patch('artex.skills.catalog_read.ScopedFilesystem') as fs:
            self.assertEqual(self.request(auth=False)[0],401);sql.assert_not_called();fs.assert_not_called()
    def test_foreign_base_and_pool_refused(self):
        from test_intercept_read_setup import Tests as Other
        other=Other();other.setUp();self.addCleanup(other.doCleanups)
        with self.assertRaises(RuntimeError):RuntimeListener(self.r.bundle,self.r.owner,skill_reads=self.build(base=other.original))
        with self.assertRaises(ValueError):RuntimeListener(self.r.bundle,self.r.owner,skill_reads=self.build(pool=other.db))
    def test_real_start_response_revokes_before_body(self):
        for path in ('/api/skills/sample/files','/api/skills/sample/files/SKILL.md','/api/skills'):
            with patch.object(self.r.pool,'fetch_all',return_value=[]):
                status,body=self.request(path,start=lambda:setattr(self,'allowed',False))
            self.assertEqual(status,200);self.assertEqual(body,b'');self.allowed=True
    def test_real_start_response_tree_mutation(self):
        with patch.object(self.r.pool,'fetch_all',return_value=[]):
            status,body=self.request(start=lambda:(self.c.c.f.skill/'new').write_text('new'))
        self.assertEqual(status,200);self.assertEqual(body,b'')
    def test_read_overlay_preserved(self):
        from artex.server.task_runtime_reads import TaskRuntimeReads,TaskReadScope
        reads=TaskRuntimeReads(self.r.pool,TaskReadScope(5,9,'r'),authorize=lambda *a:False,current=lambda *a:True,lifecycle_guard=lambda s:nullcontext(),runtime_snapshot=lambda s:None)
        bundle=replace(self.r.bundle,read_app=reads.bind(self.r.bundle.bootstrap.app))
        listener=RuntimeListener(bundle,self.r.owner,skill_reads=self.skills)
        self.assertEqual(self.request('/api/tasks/5',listener=listener)[0],403)
        self.assertEqual(self.request('/api/health',auth=False,listener=listener)[0],200)
        self.assertEqual(self.request('/api/skills/sample/files',listener=listener)[0],200)
    def test_prebind_composer_and_postheader_swap(self):
        with patch.object(_SkillReadApplication,'__call__',lambda *a:[]):
            with self.assertRaises(RuntimeError):RuntimeListener(self.r.bundle,self.r.owner,skill_reads=self.skills)
        status,body=self.request('/api/skills/sample/files',start=lambda:setattr(self.skills.application,'files_application',None))
        self.assertEqual(status,200);self.assertEqual(body,b'')
    def test_prebind_adapter_implementation_refused(self):
        from artex.server.runtime_listener import _RuntimeSkillView
        for name in ('__init__','__call__','validate'):
            with patch.object(_RuntimeSkillView,name,lambda *a:None):
                with self.assertRaises(ValueError):RuntimeListener(self.r.bundle,self.r.owner,skill_reads=self.skills)
    def test_replaced_fallback_refused_before_unauthenticated_delegate(self):
        calls=[]
        def forged(env,start):
            calls.append(1);start('200 OK',[]);return [b'UNAUTHORIZED']
        self.listener._skill_view.runtime_view=forged
        status,body=self.request('/api/tasks',auth=False)
        self.assertEqual(status,503);self.assertNotIn(b'UNAUTHORIZED',body);self.assertEqual(calls,[])
    def test_replaced_check_cannot_hide_fallback_swap(self):
        calls=[];view=self.listener._skill_view
        view.check=lambda:None
        view.runtime_view=lambda *a:calls.append(1) or []
        status,body=self.request('/api/tasks',auth=False)
        self.assertEqual(status,503);self.assertFalse(calls)
    def test_shutdown_owned_resources(self):
        self.assertTrue(self.listener.stop(1).stopped);self.assertEqual(self.r.calls,['admissions','cancel','resources'])

if __name__=='__main__':unittest.main()
