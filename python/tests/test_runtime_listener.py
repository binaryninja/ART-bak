"""Inert listener lifecycle; socket/server boundaries mocked, no actual bind."""
import unittest,io
from unittest.mock import patch,Mock
from artex.server.runtime_listener import RuntimeListener,RuntimeResourceOwner,DevListenerConfig,QuietRuntimeHandler
from artex.db.pool import ExecutorPool
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.agent.task_providers import TaskProviderResolver
from artex.server.task_runtime_setup import build_task_runtime_application
from artex.agent.host import DrainResult,PollResult
from test_application import TestCodec
import test_task_runtime_application as fixtures

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.pool=ExecutorPool(lambda:self.f.f.db,max_size=2)
        # Preserve fake DB close semantics without touching any live resource.
        self.f.f.db.close=lambda:None
        p=self.f.p
        for store in (p.tasks,p.agents,p.profiles):store.executor=self.pool
        r=self.f.runtime
        resolver=TaskProviderResolver(p.tasks,p.agents,p.profiles,p.factory,current=lambda s:True,
            lifecycle_guard=r._guard,authorize_task=lambda *a:True,authorize_profile=lambda *a:True)
        runtime=TaskRuntimeAssembly(self.pool,providers=resolver,transcript=r.transcript,prompts=r._prompts,
            policy_revision=r._revision,current=r._current,lifecycle_guard=r._guard,authorize=r._authorize,
            asset_authorize=r._assets,validate_input=r._input,graph_catalog=r._catalog)
        self.bundle=build_task_runtime_application(runtime,b'x'*32,TestCodec(),live_last_activity=lambda id:0,profile_available=lambda id:True,single_process=True)
        self.calls=[]
        self.owner=RuntimeResourceOwner(self.pool,lambda:self.calls.append('admissions') or True,
            lambda:self.calls.append('cancel') or True,lambda:self.calls.append('resources') or True)
    def test_construction_inert_and_exact_pool_required(self):
        with patch('artex.server.runtime_listener.make_server') as server:
            listener=RuntimeListener(self.bundle,self.owner)
            self.assertEqual(self.calls,[]);server.assert_not_called();self.assertEqual(self.f.p.calls,[])
        other=ExecutorPool(lambda:None)
        with self.assertRaises(ValueError):RuntimeListener(self.bundle,RuntimeResourceOwner(other,self.owner.stop_admissions,self.owner.cancel_owned,self.owner.close_resources))
    def test_start_loopback_only_no_pump(self):
        with patch('artex.server.runtime_listener.make_server') as server,patch('artex.server.runtime_listener.threading.Thread') as thread,patch.object(self.bundle.pump,'start') as pump:
            listener=RuntimeListener(self.bundle,self.owner,DevListenerConfig(port=8099))
            self.assertTrue(listener.start_listener());self.assertFalse(listener.start_listener())
            self.assertEqual(server.call_args.args[:2],('127.0.0.1',8099));pump.assert_not_called()
            self.assertEqual(self.f.p.calls,[]);self.assertEqual(self.calls,[])
    def test_unresolved_drain_retains_resources_and_pool(self):
        listener=RuntimeListener(self.bundle,self.owner)
        with patch.object(self.bundle.host,'drain',return_value=DrainResult(False,PollResult(True))),patch.object(self.pool,'close') as close:
            result=listener.stop(0)
            self.assertFalse(result.stopped);self.assertNotIn('resources',self.calls);close.assert_not_called()
        with self.assertRaises(RuntimeError):listener.start_pump()
    def test_clean_shutdown_order_and_idempotency(self):
        listener=RuntimeListener(self.bundle,self.owner)
        result=listener.stop(1)
        self.assertTrue(result.stopped,result);self.assertEqual(self.calls,['admissions','cancel','resources'])
        self.assertTrue(listener.stop(0).stopped);self.assertEqual(len(self.calls),3)
    def test_invalid_configuration_before_bind_or_policy(self):
        for config in (DevListenerConfig(port=0),DevListenerConfig(max_connections=True),DevListenerConfig(socket_timeout=float('nan'))):
            with self.assertRaises(ValueError):RuntimeListener(self.bundle,self.owner,config)
        self.assertEqual(self.calls,[])

class HandlerTests(unittest.TestCase):
    def parse(self,headers):
        handler=object.__new__(QuietRuntimeHandler)
        handler.rfile=io.BytesIO(headers+b'\r\n');handler.raw_requestline=b'POST /api/tasks HTTP/1.1\r\n'
        handler.wfile=io.BytesIO();handler.client_address=('127.0.0.1',1)
        handler.server=Mock(body_limit=1024,config=DevListenerConfig(port=8787));handler.request_version='HTTP/1.1';handler.close_connection=True
        handler.send_error=Mock()
        return handler,handler.parse_request()
    def test_duplicate_auth_length_and_transfer_encoding_refused(self):
        for headers in (b'Authorization: a\r\nAuthorization: b\r\n',b'Content-Length: 1\r\nContent-Length: 1\r\n',b'Transfer-Encoding: chunked\r\n',b'Content-Length: 9999\r\n'):
            handler,valid=self.parse(headers);self.assertFalse(valid);handler.send_error.assert_called()
    def test_valid_framing_and_quiet_errors(self):
        handler,valid=self.parse(b'Host: localhost:8787\r\nContent-Length: 0\r\n')
        self.assertTrue(valid);self.assertTrue(handler.close_connection)
        self.assertEqual(handler.get_stderr().write('secret'),6)

class OriginTests(HandlerTests):
    def test_host_and_origin_are_exact_local_authority(self):
        for headers in (b'Host: attacker.invalid\r\n',b'Host: localhost:8787\r\nOrigin: http://evil.invalid\r\n',b'Host: localhost:8787\r\nOrigin: null\r\n',b'Host: localhost:8787\r\nOrigin: http://localhost:3000\r\n'):
            _,valid=self.parse(headers);self.assertFalse(valid)
        _,valid=self.parse(b'Host: localhost:8787\r\nOrigin: http://localhost:8787\r\n');self.assertTrue(valid)

class AuthBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f=Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def environ(self,path):
        from artex.server.auth import sign_jwt
        return {'REQUEST_METHOD':'GET','PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32)}
    def test_final_json_auth_replacement_withholds_response(self):
        from artex.server.core import json_response
        router=self.f.bundle.bootstrap.app.router
        def handler(req,p):
            response=json_response(200,{'private':'fixture'})
            router.authorize=lambda req:True
            return response
        router.add('GET','/probe',handler)
        listener=RuntimeListener(self.f.bundle,self.f.owner);statuses=[]
        body=b''.join(listener._view(self.environ('/probe'),lambda s,h:statuses.append(s)))
        self.assertEqual(statuses,['503 Service Unavailable']);self.assertNotIn(b'fixture',body)
    def test_auth_change_between_stream_chunks_stops_before_next(self):
        from artex.server.streaming import StreamingResponse
        router=self.f.bundle.bootstrap.app.router;steps=[]
        def chunks():
            try:
                steps.append(1);yield b'first'
                steps.append(2);yield b'private-second'
            finally:steps.append('closed')
        router.add('GET','/stream',lambda req,p:StreamingResponse(200,chunks(),{'Content-Type':'text/event-stream'}))
        listener=RuntimeListener(self.f.bundle,self.f.owner);statuses=[]
        stream=listener._view(self.environ('/stream'),lambda s,h:statuses.append(s))
        self.assertEqual(next(stream),b'first');router.authorize=lambda req:True
        self.assertEqual(list(stream),[]);self.assertEqual(steps,[1,'closed'])
    def test_overlay_uses_explicit_bootstrap_auth_not_closure_introspection(self):
        from dataclasses import replace
        from contextlib import nullcontext
        from artex.server.task_runtime_reads import TaskRuntimeReads,TaskReadScope
        f=self.f
        reads=TaskRuntimeReads(f.pool,TaskReadScope(5,9,'r'),authorize=lambda *a:True,current=lambda s:True,
            lifecycle_guard=lambda s:nullcontext(),runtime_snapshot=lambda s:None)
        bundle=replace(f.bundle,read_app=reads.bind(f.bundle.bootstrap.app))
        listener=RuntimeListener(bundle,f.owner)
        bundle.bootstrap.app.router.authorize=lambda request:True
        with patch('artex.server.runtime_listener.make_server') as bind:
            with self.assertRaises(RuntimeError):listener.start_listener()
            bind.assert_not_called()
