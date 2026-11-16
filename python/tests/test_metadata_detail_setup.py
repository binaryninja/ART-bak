"""Optional detail composition, real JWT and synthetic recorded rows only."""
import unittest,io,json
from urllib.parse import urlsplit
from artex.server.intercept_read_setup import bind_metadata_reads
from artex.server.auth import sign_jwt
import test_intercept_detail_read as detail_fixture
import test_intercept_read_setup as app_fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=app_fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.d=detail_fixture.Tests();self.d.setUp();self.addCleanup(self.d.doCleanups)
        self.bundle=self.bind()
    def bind(self):return bind_metadata_reads(self.f.original,detail_service=self.d.service,authorize_global=lambda r,k:True)
    def request(self,target='/api/intercept/history/7',*,token=True,method='GET',start=None):
        u=urlsplit(target);statuses=[]
        env={'REQUEST_METHOD':method,'PATH_INFO':u.path,'QUERY_STRING':u.query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(self.f.key,now=1000.)
        def respond(status,headers):
            statuses.append(status)
            if start:start()
        body=b''.join(self.bundle.application(env,respond))
        return int(statuses[0].split()[0]),body
    def test_optional_inert_auth_then_original_record(self):
        self.assertFalse(self.d.queries)
        self.assertEqual(self.request(token=False)[0],401);self.assertFalse(self.d.queries)
        status,body=self.request();self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['id'],7);self.assertEqual(self.d.queries[0].params[:2],(7,'opaque-task'))
        self.assertEqual(self.request('/api/intercept/history/7/execution')[0],404)
        self.assertEqual(self.request(method='POST')[0],405)
        self.assertEqual(self.request(method='HEAD'),(400,b''))
    def test_malformed_selection_never_queries(self):
        for path in ('0','%37','9223372036854775808','7?task=foreign'):
            self.assertEqual(self.request('/api/intercept/history/'+path)[0],400)
        self.assertFalse(self.d.queries)
    def test_final_start_response_grant_revocation_blocks_body(self):
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self.d,'raw',False))
        self.assertEqual(len(self.d.queries),1)
    def test_dynamic_and_literal_conflicts_rejected_atomically(self):
        for method,path in (('GET','/api/intercept/history/7'),('HEAD','/api/intercept/history/{key}'),('GET','/api/{kind}/history/{id}')):
            f=app_fixture.Tests();f.setUp()
            try:
                f.original.router.add(method,path,lambda r,p:None);before=tuple(f.original.router._routes)
                with self.assertRaises(ValueError):bind_metadata_reads(f.original,detail_service=self.d.service,authorize_global=lambda *a:True)
                self.assertEqual(tuple(f.original.router._routes),before)
            finally:f.doCleanups()
    def test_mismatched_injected_selection_rejected(self):
        self.assertEqual(self.d.service.handle(self.d.request,selection=8).status,400)
        self.assertEqual(self.d.service.handle(self.d.request,selection=True).status,400)
        self.assertFalse(self.d.queries)
