import unittest,io
from copy import copy
from urllib.parse import urlsplit
from artex.server.auth import AuthService,sign_jwt
from artex.server.core import Router,Request,json_response
from artex.server.wsgi import WSGIApplication
from artex.server.task_report_setup import build_task_report_application
from artex.server.task_report import PreparedReport
from test_application import TestCodec
import test_task_report as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.now=1000.;self.key=b'r'*32
        self.auth=AuthService(object(),TestCodec(),self.key,clock=lambda:self.now)
        self.original=WSGIApplication(self.auth.router())
        self.bundle=build_task_report_application(self.original,self.f.service)
    def request(self,target='/api/report?task=5',*,token=True,start=None,application=None,method='GET',body=b''):
        u=urlsplit(target);state=[]
        env={'REQUEST_METHOD':method,'PATH_INFO':u.path,'QUERY_STRING':u.query,'CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body)}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(self.key,now=1000.)
        def response(status,headers):
            state.append((status,headers))
            if start:start()
        chunks=(application or self.bundle.application)(env,response)
        return int(state[0][0].split()[0]),b''.join(chunks)
    def test_optional_inert_original_app_unchanged(self):
        self.assertEqual(self.f.db.calls,[])
        self.assertEqual(self.request(application=self.original)[0],404)
        before=tuple(self.original.router._routes)
        status,body=self.request();self.assertEqual(status,200);self.assertIn(b'Local finding',body)
        self.assertEqual(tuple(self.original.router._routes),before)
    def test_jwt_before_reads_and_exact_fixed_selector(self):
        self.assertEqual(self.request(token=False)[0],401);self.assertFalse(self.f.db.calls)
        self.assertEqual(self.request('/api/report?task=6')[0],400);self.assertFalse(self.f.db.calls)
        self.f.assets=False;self.assertEqual(self.request()[0],403);self.assertFalse(self.f.db.calls)
    def test_original_jwt_swap_during_query_discards_body(self):
        self.f.db.after=lambda q:setattr(self.original.router,'authorize',lambda r:True)
        status,body=self.request();self.assertEqual(status,403);self.assertNotIn(b'Local finding',body)
    def test_after_headers_grant_and_jwt_expiry_abort_bytes(self):
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self.f,'assets',False))
        self.f.assets=True
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self,'now',10000000000.))
    def test_prepared_original_issued_receipt_only(self):
        request=Request('GET','/api/report?task=5')
        receipt=self.f.service.prepare(request)
        self.assertTrue(self.f.service.validate_disclosure(receipt))
        for fake in (copy(receipt),PreparedReport(receipt.response)):
            with self.assertRaises(Exception):self.f.service.validate_disclosure(fake)
        object.__setattr__(receipt.response,'body',b'foreign')
        with self.assertRaises(Exception):self.f.service.validate_disclosure(receipt)
    def test_conflict_no_partial_install_and_arbitrary_auth_denied(self):
        self.original.router.add('GET','/api/{kind}',lambda r,p:json_response(200,{}))
        before=tuple(self.original.router._routes)
        with self.assertRaises(ValueError):build_task_report_application(self.original,self.f.service)
        self.assertEqual(tuple(self.original.router._routes),before)
        with self.assertRaises(ValueError):build_task_report_application(WSGIApplication(Router(lambda r:True)),self.f.service)
        self.assertFalse(self.f.db.calls)
    def test_head_and_body_transport_contract(self):
        status,body=self.request(method='HEAD');self.assertEqual(status,200);self.assertEqual(body,b'')
        self.assertEqual(self.request(body=b'x')[0],400)
