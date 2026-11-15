"""All connections are fakes; no DNS, socket, API credential or network use."""
import json
import socket
import ssl
from unittest.mock import patch
import unittest

from artex.providers import Config, Provider, HTTPError
from artex.providers.http import HTTPTransport, TransportPolicyError, TransportProtocolError, TransportCancelled, TransportTimeout, TransportError


class FakeSocket:
    def __init__(self): self.timeouts=[]
    def settimeout(self,value): self.timeouts.append(value)


class FakeResponse:
    def __init__(self, body=b'{}',status=200,headers=None,on_read=None):
        self.body,self.status,self.headers=body,status,headers or {}
        self.closed=0; self.reads=[]; self.on_read=on_read
    def getheader(self,name): return self.headers.get(name)
    def read1(self,size):
        self.reads.append(size)
        if self.on_read: self.on_read()
        data,self.body=self.body[:size],self.body[size:]
        return data
    def close(self): self.closed+=1


class FakeConnection:
    def __init__(self,response=None,error=None):
        self.response=response or FakeResponse(); self.error=error
        self.closed=0; self.calls=[]; self.sock=FakeSocket(); self.timeout=None
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        if self.error: raise self.error
    def getresponse(self): return self.response
    def close(self): self.closed+=1


def transport(**kwargs):
    return HTTPTransport(['https://provider.example'],**kwargs)


def send(t,context=None,stream=False,headers=None,url='https://provider.example/v1/messages',body=b'{}'):
    return t(context,url,headers if headers is not None else {'authorization':'Bearer test-only'},body,stream)


class HTTPTransportTests(unittest.TestCase):
    def setUp(self):
        # Even a regression in preflight rejection must never reach a network.
        for constructor in ('HTTPConnection', 'HTTPSConnection'):
            patcher=patch('artex.providers.http.http.client.'+constructor,
                          side_effect=AssertionError('Unexpected connection constructor'))
            patcher.start(); self.addCleanup(patcher.stop)

    def test_verified_https_direct_no_environment_proxy(self):
        conn=FakeConnection(FakeResponse(b'{"ok":true}',headers={'Content-Length':'11'}))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn) as factory, patch.dict('os.environ',{'HTTPS_PROXY':'http://untrusted.invalid:8080','HTTP_PROXY':'http://untrusted.invalid:8080'}):
            result=send(transport())
        self.assertEqual(result.body,b'{"ok":true}')
        self.assertEqual(factory.call_args.args,('provider.example',443))
        context=factory.call_args.kwargs['context']
        self.assertTrue(context.check_hostname); self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertEqual(conn.calls[0][0],('POST','/v1/messages'))
        self.assertEqual(conn.calls[0][1]['headers']['accept-encoding'],'identity')
        self.assertEqual((conn.closed,conn.response.closed),(1,1))
        result.close(); self.assertEqual(conn.closed,1)

    def test_origin_and_url_policy_before_connection(self):
        t=transport()
        urls=['https://other.example/v1','https://provider.example:444/v1','https://provider.example@evil.example/v1',
              'https://provider.example./v1','https://provider.example/v1#frag','https://provider.example:0/v1',
              'https://provider.example\\@evil.example/v1','https://provider.example/\nsecret','http://provider.example/v1']
        with patch('artex.providers.http.http.client.HTTPSConnection') as factory:
            for url in urls:
                with self.subTest(url=url),self.assertRaises(TransportPolicyError): send(t,url=url)
            factory.assert_not_called()

    def test_http_requires_explicit_literal_loopback_optin(self):
        for origin,opts in [('http://127.0.0.1:8000',{}),('http://localhost:8000',{'allow_loopback_http':True}),('http://10.0.0.1',{'allow_loopback_http':True})]:
            with self.assertRaises(TransportPolicyError): HTTPTransport([origin],**opts)
        for origin,host,port in [('http://127.0.0.1:8000','127.0.0.1',8000),('http://[::1]:8000','::1',8000)]:
            conn=FakeConnection()
            with patch('artex.providers.http.http.client.HTTPConnection',return_value=conn) as factory:
                send(HTTPTransport([origin],allow_loopback_http=True),url=origin+'/v1')
            self.assertEqual(factory.call_args.args,(host,port))

    def test_insecure_or_mutated_tls_context_rejected(self):
        context=ssl.create_default_context(); context.check_hostname=False
        with self.assertRaises(TransportPolicyError): transport(tls_context=context)
        context=ssl.create_default_context(); t=transport(tls_context=context); context.check_hostname=False
        with patch('artex.providers.http.http.client.HTTPSConnection') as factory:
            with self.assertRaises(TransportPolicyError): send(t)
            factory.assert_not_called()

    def test_redirect_never_followed_and_body_not_read(self):
        response=FakeResponse(b'sensitive body',302,{'Location':'https://elsewhere.example/steal'})
        conn=FakeConnection(response)
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn) as factory:
            result=send(transport())
        self.assertEqual((result.status,result.body),(302,b''))
        self.assertEqual(factory.call_count,1)
        self.assertFalse(response.reads)
        self.assertEqual(conn.closed,1)

    def test_headers_body_bounds_and_injection(self):
        t=transport(max_request_bytes=2)
        for headers in ({'Host':'evil.example'},{'authorization':'secret\r\nHost: evil'}, {'content-length':'3'}, {'proxy-authorization':'secret'}, {'accept-encoding':'gzip'}, {'x':'a'*16385}):
            with self.assertRaises(TransportPolicyError): send(t,headers=headers)
        with self.assertRaises(TransportPolicyError): send(t,body=b'123')
        with self.assertRaises(TransportPolicyError): send(t,body='{}')

    def test_declared_oversize_compression_and_ambiguous_length(self):
        for headers in ({'Content-Length':'9'},{'Content-Encoding':'gzip'},{'Content-Length':'2','Transfer-Encoding':'chunked'},{'Content-Length':'x'},{'Transfer-Encoding':'unknown'}):
            response=FakeResponse(b'{}',headers=headers);conn=FakeConnection(response)
            with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn),self.assertRaises(TransportProtocolError):
                send(transport(max_response_bytes=8))
            self.assertEqual((conn.closed,response.closed),(1,1));self.assertFalse(response.reads)

    def test_unannounced_oversize_and_truncated_body(self):
        for response in (FakeResponse(b'123456789'),FakeResponse(b'12',headers={'Content-Length':'4'})):
            conn=FakeConnection(response)
            with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn),self.assertRaises(TransportProtocolError):
                send(transport(max_response_bytes=8,chunk_size=3))
            self.assertEqual(conn.closed,1)

    def test_stream_is_lazy_bounded_and_explicitly_closed(self):
        response=FakeResponse(b'abcdef');conn=FakeConnection(response)
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            result=send(transport(chunk_size=2),stream=True)
        self.assertFalse(response.reads)
        self.assertEqual(next(iter(result.body)),b'ab')
        result.close(); self.assertEqual(conn.closed,1)
        self.assertEqual(list(result.body),[])

    def test_stream_returned_inside_exception_handler_stays_open(self):
        conn=FakeConnection(FakeResponse(b'abc'))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            try:
                raise ValueError('unrelated outer error')
            except ValueError:
                result=send(transport(),stream=True)
                self.assertEqual(conn.closed,0)
                self.assertEqual(next(result.body),b'abc')
                result.close()
        self.assertEqual(conn.closed,1)

    def test_stream_limit_closes_resources(self):
        conn=FakeConnection(FakeResponse(b'12345'))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            result=send(transport(max_response_bytes=4,chunk_size=2),stream=True)
        self.assertEqual(next(result.body),b'12')
        self.assertEqual(next(result.body),b'34')
        with self.assertRaises(TransportProtocolError): next(result.body)
        self.assertEqual(conn.closed,1)

    def test_cancellation_before_request_and_after_read(self):
        with patch('artex.providers.http.http.client.HTTPSConnection') as factory:
            with self.assertRaises(TransportCancelled): send(transport(),{'cancelled':True})
            factory.assert_not_called()
        ctx={}
        conn=FakeConnection(FakeResponse(b'abc',on_read=lambda:ctx.update(cancelled=True)))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            result=send(transport(),ctx,stream=True)
        with self.assertRaises(TransportCancelled):next(result.body)
        self.assertEqual(conn.closed,1)
        class Runtime:
            def check(self): raise RuntimeError('private cancel context')
        with self.assertRaisesRegex(TransportCancelled,'context canceled'):send(transport(),{'runtime_context':Runtime()})

    def test_deadline_and_network_errors_are_redacted_and_closed(self):
        for error,expected in [(socket.timeout('secret timeout'),TransportTimeout),(ssl.SSLError('private certificate'),TransportError),(OSError('https://secret-token@example.test'),TransportError)]:
            conn=FakeConnection(error=error)
            with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn),self.assertRaises(expected) as caught:
                send(transport())
            self.assertNotIn('secret',str(caught.exception));self.assertNotIn('private',str(caught.exception))
            self.assertEqual(conn.closed,1)
        now=[0];conn=FakeConnection(FakeResponse(b'abc',on_read=lambda:now.__setitem__(0,5)))
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            result=send(transport(total_timeout=1,clock=lambda:now[0]),stream=True)
        with self.assertRaises(TransportTimeout):next(result.body)
        self.assertEqual(conn.closed,1)

    def test_concrete_provider_uses_transport_without_real_connection(self):
        payload=json.dumps({'choices':[{'message':{'content':'test reply'},'finish_reason':'stop'}]}).encode()
        conn=FakeConnection(FakeResponse(payload))
        p=Provider(Config('openai','fixture',base_url='https://provider.example/v1',api_key='placeholder'),transport())
        with patch('artex.providers.http.http.client.HTTPSConnection',return_value=conn):
            message,stop,usage=p.complete({}, {'messages':[{'role':'user','content':[{'type':'text','text':'inert'}]}]})
        self.assertEqual(message['content'][0]['text'],'test reply')
        self.assertEqual(stop,'end_turn');self.assertEqual(conn.closed,1)
        self.assertEqual(conn.calls[0][0][1],'/v1/chat/completions')

    def test_config_validation(self):
        for origins in ([], 'https://provider.example', ['https://provider.example/path']):
            with self.assertRaises(TransportPolicyError):HTTPTransport(origins)
        for opts in ({'timeout':0},{'total_timeout':float('nan')},{'max_response_bytes':0},{'chunk_size':True}):
            with self.assertRaises(ValueError):transport(**opts)


if __name__=='__main__':unittest.main()
