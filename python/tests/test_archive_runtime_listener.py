import io,json,unittest
from contextlib import nullcontext
from unittest.mock import patch
from artex.server.runtime_listener import RuntimeListener
from artex.server.archive_catalog import ArchiveCatalogService
from artex.server.auth import sign_jwt
from test_runtime_listener import Tests as Fixture
from test_archive_catalog import item

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=True;self.reads=[]
        self.service=ArchiveCatalogService(self.f.pool,workspace='fixture',authorize=lambda w:self.allowed,
            request_authorize=lambda r,w:True,lifecycle_guard=lambda w:nullcontext())
        from artex.server.core import json_response
        self.f.bundle.bootstrap.app.router.add('GET','/original',lambda *a:json_response(200,{'original':True}))
        self.listener=RuntimeListener(self.f.bundle,self.f.owner,archive_catalog=self.service)
    def call(self,path='/api/task-archives',headers=lambda:None,method='GET'):
        statuses=[]
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),
            'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
        def start(s,h):statuses.append(s);headers()
        def fetch(q):self.reads.append(q);return [(1,json.dumps(item()))]
        with patch.object(self.f.pool,'fetch_all',side_effect=fetch):body=b''.join(self.listener._view(env,start))
        return int(statuses[0].split()[0]),body
    def test_inert_metadata_and_fallback(self):
        self.assertEqual(self.f.calls,[]);self.assertEqual(self.f.f.p.calls,[])
        status,body=self.call();self.assertEqual(status,200);self.assertEqual(json.loads(body)['total'],1)
        self.assertEqual(len(self.reads),1)
        self.assertEqual(self.call('/original')[0],200)
        self.assertEqual(self.call('/api/task-archives/1/restore',method='POST')[0],404)
        self.assertEqual(len(self.reads),1)
    def test_postheader_revocation_and_pool_change(self):
        status,body=self.call(headers=lambda:setattr(self,'allowed',False))
        self.assertEqual((status,body),(200,b''));self.allowed=True
        status,body=self.call(headers=lambda:setattr(self.service,'executor',object()))
        self.assertEqual((status,body),(200,b''))
    def test_wrong_pool_and_changed_fallback_refused(self):
        self.service.executor=object()
        with self.assertRaises(ValueError):RuntimeListener(self.f.bundle,self.f.owner,archive_catalog=self.service)
        self.service.executor=self.f.pool
        called=[];self.listener._archive_view.fallback=lambda *a:called.append(True) or []
        self.assertEqual(self.call('/api/tasks')[0],503);self.assertEqual(called,[])
    def test_changed_method_refused_before_binding(self):
        with patch.object(ArchiveCatalogService,'handle',lambda *a:None):
            with self.assertRaises(ValueError):RuntimeListener(self.f.bundle,self.f.owner,archive_catalog=self.service)
    def test_clean_stop_uses_original_owner(self):
        self.assertTrue(self.listener.stop(1).stopped)
        self.assertEqual(self.f.calls,['admissions','cancel','resources'])
    def test_existing_skill_composer_and_read_fallback_preserved(self):
        import test_runtime_skill_listener as skills
        fixture=skills.Tests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        service=ArchiveCatalogService(fixture.r.pool,workspace='fixture',authorize=lambda w:True,
            request_authorize=lambda r,w:True,lifecycle_guard=lambda w:nullcontext())
        listener=RuntimeListener(fixture.r.bundle,fixture.r.owner,skill_reads=fixture.skills,archive_catalog=service)
        with patch.object(fixture.r.pool,'fetch_all',return_value=[]):
            self.assertEqual(fixture.request('/api/skills/sample/files',listener=listener)[0],200)
            self.assertEqual(fixture.request('/api/health',auth=False,listener=listener)[0],200)
            status,body=fixture.request('/api/skills/sample/files',listener=listener,start=lambda:setattr(fixture,'allowed',False))
            self.assertEqual((status,body),(200,b''))
        with patch.object(fixture.r.pool,'fetch_all',return_value=[(1,json.dumps(item()))]):
            self.assertEqual(fixture.request('/api/task-archives',listener=listener)[0],200)
        leaf=listener._skill_view.runtime_view;called=[]
        leaf.app=lambda *a:called.append(True) or []
        self.assertEqual(fixture.request('/api/tasks',auth=False,listener=listener)[0],503)
        self.assertEqual(called,[])
    def test_unresolved_http_drain_retains_pool_and_resources(self):
        from unittest.mock import Mock
        server=Mock();server.quiesce.return_value=False
        self.listener._server=server
        with patch.object(self.f.pool,'close') as close:
            result=self.listener.stop(.01)
            self.assertFalse(result.stopped);self.assertEqual(result.reason,'http_unresolved')
            close.assert_not_called();self.assertEqual(self.f.calls,['admissions'])

    def test_inner_fallback_mutation_refused_without_forged_call(self):
        leaf=self.listener._archive_view.fallback
        for name in ('app','check'):
            original=getattr(leaf,name);called=[]
            setattr(leaf,name,lambda *a:called.append(True) or [])
            self.assertEqual(self.call('/api/tasks')[0],503);self.assertEqual(called,[])
            setattr(leaf,name,original)

    def test_inner_fallback_implementation_mutation_refused(self):
        leaf=self.listener._archive_view.fallback
        for obj,name in ((leaf.app,'_handle'),(leaf.app.router,'dispatch')):
            called=[]
            with patch.object(obj,name,lambda *a:called.append(True) or None):
                self.assertEqual(self.call('/api/tasks')[0],503);self.assertEqual(called,[])
    def test_prebind_replaced_runtime_implementation_refused(self):
        with patch.object(self.f.bundle.app,'_handle',lambda *a:None):
            with self.assertRaises(ValueError):RuntimeListener(self.f.bundle,self.f.owner,archive_catalog=self.service)
