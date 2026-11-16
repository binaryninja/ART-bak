"""Independent inert listener boundary tests; no socket binding."""
import unittest
from unittest.mock import patch
import test_runtime_listener as fixture
from artex.server.runtime_listener import RuntimeListener,RuntimeResourceOwner

class ListenerReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_original_jwt_authorizer_change_blocks_listener_start(self):
        f=self.f;listener=RuntimeListener(f.bundle,f.owner)
        f.bundle.bootstrap.app.router.authorize=lambda request:True
        with patch('artex.server.runtime_listener.make_server') as server,patch('artex.server.runtime_listener.threading.Thread'):
            with self.assertRaises(RuntimeError):listener.start_listener()
            server.assert_not_called()
    def test_reentrant_shutdown_callback_does_not_close_twice(self):
        f=self.f;results=[];ref=[]
        def stop():results.append(ref[0].stop(0));return True
        owner=RuntimeResourceOwner(f.pool,stop,f.owner.cancel_owned,f.owner.close_resources)
        listener=RuntimeListener(f.bundle,owner);ref.append(listener)
        result=listener.stop(1)
        self.assertTrue(result.stopped);self.assertEqual(results[0].reason,'shutdown_busy')
        self.assertEqual(f.calls.count('resources'),1)
    def test_failed_admission_fence_never_closes_pool(self):
        f=self.f;owner=RuntimeResourceOwner(f.pool,lambda:False,f.owner.cancel_owned,f.owner.close_resources)
        listener=RuntimeListener(f.bundle,owner)
        with patch.object(f.pool,'close') as close:
            result=listener.stop(0)
            self.assertFalse(result.stopped);close.assert_not_called()
        self.assertEqual(f.calls,[])

class FramingReview(unittest.TestCase):
    def test_raw_network_path_target_rejected_before_wsgi_normalization(self):
        import io
        from unittest.mock import Mock
        from artex.server.runtime_listener import QuietRuntimeHandler
        handler=object.__new__(QuietRuntimeHandler)
        handler.rfile=io.BytesIO(b'Host: localhost\r\n\r\n')
        handler.raw_requestline=b'GET //api/tasks HTTP/1.1\r\n'
        handler.wfile=io.BytesIO();handler.client_address=('127.0.0.1',1)
        handler.server=Mock(body_limit=1024);handler.request_version='HTTP/1.1';handler.close_connection=True
        handler.send_error=Mock()
        self.assertFalse(handler.parse_request())
        handler.send_error.assert_called()

class StreamingBoundaryReview(unittest.TestCase):
    def test_revoked_authority_blocks_underlying_next_not_just_chunk(self):
        from artex.server.runtime_listener import _AuthenticatedView
        live=[True];advances=[];closed=[]
        def check():
            if not live[0]:raise RuntimeError('revoked')
        def app(env,start):
            start('200 OK',[])
            def chunks():
                try:
                    advances.append(1);yield b'one'
                    advances.append(2);yield b'two'
                finally:closed.append(True)
            return chunks()
        response=_AuthenticatedView(app,check)({},lambda *a:None)
        self.assertEqual(next(response),b'one');live[0]=False
        self.assertEqual(list(response),[])
        self.assertEqual(advances,[1]);self.assertEqual(closed,[True])

class LocalFramingReview(unittest.TestCase):
    def test_invalid_framing_rejected_after_valid_local_host(self):
        for headers,code in ((b'Authorization: a\r\nAuthorization: b\r\n',400),
                             (b'Content-Length: 1\r\nContent-Length: 2\r\n',400),
                             (b'Transfer-Encoding: chunked\r\n',400),
                             (b'Content-Length: 9999\r\n',413)):
            with self.subTest(headers=headers):
                handler,valid=fixture.HandlerTests().parse(b'Host: localhost:8787\r\n'+headers)
                self.assertFalse(valid);self.assertEqual(handler.send_error.call_args.args[0],code)
