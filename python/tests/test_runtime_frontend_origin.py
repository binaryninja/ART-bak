"""Narrow loopback frontend authority, using real inert HTTP and a test proxy.

The proxy models a rewritten Host and preserved browser Origin/Cookie; it is not
Next.js or browser execution. No database, provider or target is contacted.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import socket
import threading
import unittest
from unittest.mock import Mock
from artex.server.auth import sign_jwt
from artex.server.core import json_response
from artex.server.runtime_listener import DevListenerConfig, QuietRuntimeHandler, RuntimeListener
import test_runtime_listener as fixtures


class ConfigurationTests(unittest.TestCase):
    def test_one_canonical_http_loopback_origin_only(self):
        for origin in (None,'http://localhost','http://127.0.0.1','http://localhost:1',
                       'http://127.0.0.1:3000','http://localhost:65535'):
            with self.subTest(origin=origin):DevListenerConfig(frontend_origin=origin).validate()
        invalid=('',False,[],('http://localhost:3000',),'*','null','https://localhost:3000',
            'HTTP://localhost:3000','http://LOCALHOST:3000','http://localhost.:3000',
            'http://localhost.evil:3000','http://evil.invalid:3000','http://0.0.0.0:3000',
            'http://127.1:3000','http://2130706433:3000','http://127.0.0.2:3000',
            'http://[::1]:3000','http://localhost:0','http://localhost:80','http://localhost:03000',
            'http://localhost:65536','http://localhost:3000/','http://localhost:3000?x',
            'http://localhost:3000#x','http://a@localhost:3000','http://localhost:3000@evil.invalid',
            'http://localhost:3000\\@evil.invalid','http://local%68ost:3000','http://localhost:3000 ',
            ' http://localhost:3000','http://localhost:3000\n','http://localhost:3000,http://evil.invalid')
        for origin in invalid:
            with self.subTest(origin=origin),self.assertRaisesRegex(ValueError,'^One canonical HTTP loopback frontend origin required$'):
                DevListenerConfig(frontend_origin=origin).validate()

    def parse(self,headers,*,origin='http://localhost:3000'):
        handler=object.__new__(QuietRuntimeHandler)
        handler.rfile=io.BytesIO(headers+b'\r\n');handler.raw_requestline=b'POST /api/probe HTTP/1.1\r\n'
        handler.wfile=io.BytesIO();handler.client_address=('127.0.0.1',1)
        handler.server=Mock(body_limit=1024,config=DevListenerConfig(frontend_origin=origin))
        handler.request_version='HTTP/1.1';handler.close_connection=True;handler.send_error=Mock()
        return handler,handler.parse_request()

    def test_origin_never_grants_host_or_forwarded_header_authority(self):
        accepted=(b'Host: localhost:8787\r\nOrigin: http://localhost:3000\r\n',
                  b'Host: localhost:8787\r\nOrigin: http://localhost:8787\r\n',
                  b'Host: localhost:8787\r\nX-Forwarded-Origin: http://evil.invalid\r\n',
                  b'Host: localhost:8787\r\nOrigin: http://localhost:3000\r\nX-Forwarded-Host: evil.invalid\r\n')
        for headers in accepted:
            with self.subTest(headers=headers):self.assertTrue(self.parse(headers)[1])
        denied=(b'Host: localhost:3000\r\nOrigin: http://localhost:3000\r\n',
            b'Host: evil.invalid\r\nOrigin: http://localhost:3000\r\n',
            b'Host: localhost:8787\r\nOrigin: null\r\nX-Forwarded-Origin: http://localhost:3000\r\n',
            b'Host: localhost:8787\r\nOrigin: http://evil.invalid\r\nForwarded: host=localhost:8787;proto=http\r\n',
            b'Host: localhost:8787\r\nOrigin: http://127.0.0.1:3000\r\n',
            b'Host: localhost:8787\r\nOrigin: http://localhost:3001\r\n',
            b'Host: localhost:8787\r\nOrigin: http://localhost:3000\r\nOrigin: http://evil.invalid\r\n',
            b'Host: localhost:8787\r\nOrigin: http://localhost:3000\r\nCookie: a\r\nCookie: b\r\n',
            b'Host: localhost:8787\r\nOrigin: http://localhost:3000\r\nTransfer-Encoding: chunked\r\n')
        for headers in denied:
            with self.subTest(headers=headers):self.assertFalse(self.parse(headers)[1])
        self.assertFalse(self.parse(accepted[0],origin=None)[1])


class LoopbackProxyTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.effects=[]
        def action(request,parameters):
            self.effects.append(request.method)
            return json_response(200,{'ok':True})
        self.f.bundle.bootstrap.app.router.add('POST','/api/probe',action)
        self.f.bundle.bootstrap.app.router.add('GET','/api/probe',action)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.backend_port=sock.getsockname()[1]
        backend_port=self.backend_port
        class Proxy(BaseHTTPRequestHandler):
            # Same-origin /api forwarding with backend Host, original Origin and
            # credentials. No origin spoofing or CORS behavior is added here.
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection')}
                conn=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=2)
                try:
                    conn.request(self.command,self.path,body=body,headers=headers)
                    response=conn.getresponse();raw=response.read()
                    self.send_response(response.status)
                    self.send_header('Content-Type',response.getheader('Content-Type','text/plain'))
                    self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                finally:conn.close()
            do_GET=do_POST
            def log_message(self,*args):pass
        self.proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
        self.frontend_origin=f'http://127.0.0.1:{self.proxy.server_port}'
        self.proxy_thread=threading.Thread(target=self.proxy.serve_forever,kwargs={'poll_interval':.01})
        self.proxy_thread.start()
        def close_proxy():
            self.proxy.shutdown();self.proxy.server_close();self.proxy_thread.join(3)
        self.addCleanup(close_proxy)
        self.token=sign_jwt(b'x'*32)

    def start(self,frontend_origin):
        self.listener=RuntimeListener(self.f.bundle,self.f.owner,
            DevListenerConfig(port=self.backend_port,frontend_origin=frontend_origin))
        self.addCleanup(self.listener.stop,2)
        self.assertTrue(self.listener.start_listener())
        self.assertIsNone(self.f.bundle.pump._thread)

    def request(self,headers,*,proxy=True,method='POST'):
        port=self.proxy.server_port if proxy else self.backend_port
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        try:
            conn.request(method,'/api/probe',body=b'{}',headers=headers)
            response=conn.getresponse();body=response.read()
            self.assertIsNone(response.getheader('Access-Control-Allow-Origin'))
            self.assertIsNone(response.getheader('Access-Control-Allow-Credentials'))
            return response.status,body
        finally:conn.close()

    def test_default_reproduces_preserved_frontend_origin_refusal(self):
        self.start(None)
        status,_=self.request({'Origin':self.frontend_origin,'Authorization':'Bearer '+self.token})
        self.assertEqual(status,403);self.assertEqual(self.effects,[])
        self.assertEqual(self.f.f.p.calls,[])

    def test_opt_in_allows_exact_proxy_origin_but_preserves_jwt_and_cookie_rules(self):
        self.start(self.frontend_origin)
        cases=[
            ({'Origin':self.frontend_origin},401),
            ({'Origin':self.frontend_origin,'Authorization':'Bearer bad'},401),
            ({'Origin':self.frontend_origin,'Authorization':'Bearer '+self.token},200),
            ({'Origin':self.frontend_origin,'Cookie':'artex_token='+self.token},200),
            ({'Origin':self.frontend_origin,'Cookie':'artex_token=bad'},401),
            ({'Origin':self.frontend_origin,'Cookie':'artex_token='+self.token,'Authorization':'Bearer bad'},401),
            ({'Origin':'http://evil.invalid','Cookie':'artex_token='+self.token},403),
            ({'Origin':'null','Cookie':'artex_token='+self.token},403),
            ({'Origin':self.frontend_origin.replace('127.0.0.1','localhost'),'Cookie':'artex_token='+self.token},403),
            ({'Origin':'http://evil.invalid','X-Forwarded-Origin':self.frontend_origin,'Cookie':'artex_token='+self.token},403),
            # Absence of Origin remains legacy behavior, not a CSRF guarantee.
            ({'Cookie':'artex_token='+self.token},200),
        ]
        for headers,expected in cases:
            with self.subTest(headers={k:v if k not in ('Authorization','Cookie') else '<synthetic>' for k,v in headers.items()}):
                before=len(self.effects);status,body=self.request(headers)
                self.assertEqual(status,expected,body)
                self.assertEqual(len(self.effects)-before,int(expected==200))
        self.assertEqual(self.f.f.p.calls,[])

    def test_backend_host_and_cross_origin_preflight_are_not_enabled(self):
        self.start(self.frontend_origin)
        status,_=self.request({'Host':self.frontend_origin[7:],'Origin':self.frontend_origin,
            'Cookie':'artex_token='+self.token},proxy=False)
        self.assertEqual(status,403)
        status,_=self.request({'Origin':self.frontend_origin,'Access-Control-Request-Method':'POST',
            'Access-Control-Request-Headers':'authorization,content-type'},proxy=False,method='OPTIONS')
        self.assertNotEqual(status,200);self.assertEqual(self.effects,[])


if __name__=='__main__':unittest.main()
