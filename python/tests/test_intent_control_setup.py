import unittest,io,json
from unittest.mock import patch
from artex.agent.engine_runner import EngineRunner
from artex.server.auth import AuthService,sign_jwt
from artex.server.wsgi import WSGIApplication
from artex.server.intent_control import IntentControlService
from artex.server.intent_control_setup import build_intent_control_application
from test_application import TestCodec
import test_intent_control as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests()
        # The shared fixture ordinarily installs its direct-control adapter.
        # Omit that installation so composition performs the sole real install.
        with patch.object(EngineRunner,'install_intent_control',lambda *a:None):self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.allowed=True;self.selector_hook=None;self.key=b'i'*32
        self.auth=AuthService(object(),TestCodec(),self.key,clock=lambda:1000.)
        self.app=WSGIApplication(self.auth.router())
        def select(r,s):
            if self.selector_hook:self.selector_hook()
            return self.allowed
        self.service=IntentControlService(self.f.host,authorize_selector=select,authorize_proposal=lambda p:True,timeout=0)
        self.bundle=build_intent_control_application(self.app,self.service)
    def request(self,body=None,token=True,task='5',start=None):
        raw=json.dumps(body or {'action':'pause','reason':'','mode':'soft'}).encode();statuses=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':f'/api/tasks/{task}/intents/3/control','QUERY_STRING':'','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(self.key,now=1000.)
        def response(status,headers):
            statuses.append(status)
            if start:start()
        chunks=self.bundle.application(env,response)
        return int(statuses[0].split()[0]),json.loads(b''.join(chunks))
    def test_jwt_and_selector_before_effect(self):
        self.assertEqual(self.request(token=False)[0],401)
        self.allowed=False;self.assertEqual(self.request()[0],403)
        self.assertFalse(self.f.outer.token.cancelled)
    def test_pending_real_worker_source_fields_no_false_paused(self):
        self.f.start();status,body=self.request()
        self.assertEqual(status,409);self.assertTrue(body['pending']);self.assertFalse(body['retry'])
        self.assertFalse(self.f.outer.token.cancelled);self.assertTrue(self.service.adapter.retained(self.f.outer))
    def test_unsupported_cancel_and_no_default_route(self):
        self.assertEqual(self.request({'action':'cancel'})[0],501)
        self.assertEqual(len(self.app.router._routes),len(self.auth.router()._routes))
        with self.assertRaises(Exception):build_intent_control_application(self.app,self.service)
    def test_context_absent_denies_core_grant(self):
        self.assertFalse(self.service.adapter.authorize(None))
        self.request({'action':'cancel'})
        self.assertFalse(self.service.adapter.authorize(None))

    def test_explicit_inspection_uses_original_request_authority(self):
        from artex.server.core import Request
        self.f.start();self.assertTrue(self.request()[1]['pending'])
        self.f.release.set();self.f.thread.join(2)
        req=Request('POST','/api/tasks/5/intents/3/control',{'Authorization':'Bearer '+sign_jwt(self.key,now=1000.)})
        result=self.bundle.application.inspect(req,'5',3)
        self.assertEqual((result.phase,result.state),('confirmed','paused'))
        self.assertFalse(self.service.adapter.retained(self.f.outer))
    def test_after_headers_revocation_never_discloses_pending(self):
        self.f.start()
        with self.assertRaises(PermissionError):self.request(start=lambda:setattr(self,'allowed',False))
        self.assertTrue(self.service.adapter.retained(self.f.outer))
        self.assertFalse(self.service.adapter.authorize(None))

    def test_request_context_isolated_on_other_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        seen=[]
        def callback():
            with ThreadPoolExecutor(max_workers=1) as pool:seen.append(pool.submit(lambda:self.service.adapter.authorize(None)).result(1))
        self.selector_hook=callback
        self.assertEqual(self.request({'action':'cancel'})[0],501)
        self.assertTrue(seen);self.assertEqual(set(seen),{False})
        self.assertFalse(self.service.adapter.authorize(None))
    def test_installer_and_host_method_substitution_denied(self):
        self.f.host.control_intent=lambda *a,**k:None
        self.assertEqual(self.request()[0],403)
        self.assertFalse(self.f.outer.token.cancelled)
    def test_final_proposal_grant_revocation_retains_pending(self):
        self.f.start()
        # Original callback has an explicitly mutable underlying policy.
        # Replacement is also rejected, including after response headers.
        with self.assertRaises(PermissionError):self.request(start=lambda:setattr(self.service,'authorize_proposal',lambda p:False))
        self.assertTrue(self.service.adapter.retained(self.f.outer))
        self.assertFalse(self.service.adapter.authorize(None))
    def test_conflict_does_not_install_or_mutate_original_routes(self):
        from artex.server.core import json_response
        self.app.router.add('POST','/api/tasks/{task}/intents/{node}/control',lambda r,p:json_response(200,{}))
        before=tuple(self.app.router._routes)
        with self.assertRaises(ValueError):build_intent_control_application(self.app,self.service)
        self.assertEqual(tuple(self.app.router._routes),before)
