"""Streaming transport contracts using inert finite iterators, no listeners."""
import io
import unittest
from unittest.mock import Mock
from artex.server.core import Router
from artex.server.wsgi import WSGIApplication
from artex.server.streaming import StreamingResponse,ClosingIterator,sse_frame


class Producer:
    def __init__(self,values):self.values=iter(values);self.pulls=0;self.closes=0
    def __iter__(self):return self
    def __next__(self):
        self.pulls+=1
        value=next(self.values)
        if isinstance(value,BaseException):raise value
        return value
    def close(self):self.closes+=1


class StreamingTests(unittest.TestCase):
    def setup_response(self,producer,**kw):
        self.callback=Mock()
        self.response=StreamingResponse(kw.pop('status',200),producer,on_close=self.callback,**kw)
        self.handler=Mock(return_value=self.response)
        self.router=Router(lambda r:r.header('Authorization')=='dummy')
        self.router.add('GET','/stream',self.handler)
        self.app=WSGIApplication(self.router)
        self.started=[]

    def call(self,method='GET',auth=True,start=None):
        env=dict(REQUEST_METHOD=method,PATH_INFO='/stream',QUERY_STRING='',CONTENT_LENGTH='0')
        env['wsgi.input']=io.BytesIO()
        if auth:env['HTTP_AUTHORIZATION']='dummy'
        return self.app(env,start or (lambda s,h:self.started.append((s,dict(h)))))

    def test_lazy_no_buffering_no_content_length(self):
        p=Producer([b'one',b'two']);self.setup_response(p)
        result=self.call()
        self.assertEqual(p.pulls,0);self.assertEqual(len(self.started),1)
        self.assertNotIn('Content-Length',self.started[0][1])
        self.assertEqual(next(result),b'one');self.assertEqual(p.pulls,1)
        self.assertEqual(next(result),b'two')
        with self.assertRaises(StopIteration):next(result)
        self.assertEqual(p.closes,1);self.callback.assert_called_once_with()
        result.close();self.assertEqual(p.closes,1)

    def test_auth_before_handler_and_stream(self):
        p=Producer([b'private']);self.setup_response(p)
        self.assertNotIn(b'private',b''.join(self.call(auth=False)))
        self.assertTrue(self.started[0][0].startswith('401'))
        self.handler.assert_not_called();self.assertEqual(p.pulls,0)

    def test_disconnect_explicit_close_even_before_first_pull(self):
        for pull in (False,True):
            p=Producer([b'one',b'two']);self.setup_response(p)
            result=self.call()
            if pull:next(result)
            result.close();result.close()
            self.assertEqual(list(result),[]);self.assertEqual(p.closes,1)
            self.callback.assert_called_once_with()

    def test_head_and_no_body_status_close_without_consuming(self):
        for method,status in [('HEAD',200),('GET',101),('GET',204),('GET',304)]:
            p=Producer([b'never']);self.setup_response(p,status=status)
            self.assertEqual(list(self.call(method)),[])
            self.assertEqual(p.pulls,0);self.assertEqual(p.closes,1)
            self.callback.assert_called_once_with()
            self.assertNotIn('Content-Length',self.started[0][1])

    def test_producer_exception_no_secret_bytes_or_second_headers(self):
        p=Producer([b'visible',RuntimeError('private traceback token')]);self.setup_response(p)
        result=self.call()
        self.assertEqual(b''.join(result),b'visible')
        self.assertTrue(result.failed);self.assertEqual(p.closes,1)
        self.assertEqual(len(self.started),1);self.callback.assert_called_once_with()

    def test_invalid_and_oversized_chunks_end_closed(self):
        for bad in ('secret',bytearray(b'secret'),None,b'12345'):
            p=Producer([bad]);self.setup_response(p,max_chunk_bytes=4)
            result=self.call();self.assertEqual(list(result),[])
            self.assertTrue(result.failed);self.assertEqual(p.closes,1)

    def test_header_validation_closes_before_redacted500(self):
        p=Producer([b'secret']);self.setup_response(p,headers={'X-Test':'bad\r\nsecret'})
        self.assertEqual(b''.join(self.call()),b'{"error":"Internal server error"}\n')
        self.assertTrue(self.started[0][0].startswith('500'))
        self.assertEqual(p.pulls,0);self.assertEqual(p.closes,1);self.callback.assert_called_once()

    def test_hop_by_hop_framing_stripped(self):
        p=Producer([b'one']);self.setup_response(p,headers={'Content-Length':'999','Transfer-Encoding':'chunked',
            'Connection':'X-Secret','X-Secret':'private','X-Content-Type-Options':'unsafe','Content-Type':'text/event-stream'})
        result=self.call()
        self.assertEqual(self.started[0][1],{'Content-Type':'text/event-stream','X-Content-Type-Options':'nosniff'})
        result.close()

    def test_gateway_start_failure_closes(self):
        p=Producer([b'one']);self.setup_response(p)
        with self.assertRaises(RuntimeError):self.call(start=Mock(side_effect=RuntimeError('gateway')))
        self.assertEqual(p.pulls,0);self.assertEqual(p.closes,1);self.callback.assert_called_once()

    def test_separate_iterable_and_iterator_cleanup_once(self):
        iterator=Producer([b'one'])
        source=Mock();source.__iter__=Mock(return_value=iterator)
        source.close=Mock(side_effect=RuntimeError('private cleanup'))
        callback=Mock(side_effect=RuntimeError('private callback'))
        result=ClosingIterator(StreamingResponse(200,source,on_close=callback))
        self.assertEqual(list(result),[b'one'])
        self.assertEqual(iterator.closes,1);source.close.assert_called_once();callback.assert_called_once()
        result.close();callback.assert_called_once()

    def test_iter_creation_failure_cleanup(self):
        class Bad:
            def __iter__(self):raise RuntimeError('private')
            close=Mock()
        source=Bad();callback=Mock()
        result=ClosingIterator(StreamingResponse(200,source,on_close=callback))
        self.assertEqual(list(result),[]);self.assertTrue(result.failed)
        source.close.assert_called_once();callback.assert_called_once()

    def test_sse_multiline_data_cannot_inject_fields(self):
        self.assertEqual(sse_frame('one\r\nid: attacker\revent: bad',event='message',id='',retry=100),
            b'event: message\nid: \nretry: 100\ndata: one\ndata: id: attacker\ndata: event: bad\n\n')
        self.assertEqual(sse_frame(''),b'data: \n\n')
        self.assertEqual(sse_frame('snowman \u2603'), 'data: snowman \u2603\n\n'.encode())

    def test_sse_invalid_fields_and_byte_bounds(self):
        for key in ('event','id'):
            for value in ('bad\rvalue','bad\nvalue','bad\x00value',3):
                with self.assertRaises(ValueError):sse_frame('data',**{key:value})
        for retry in (True,-1,2**31,'100'):
            with self.assertRaises(ValueError):sse_frame('data',retry=retry)
        with self.assertRaises(ValueError):sse_frame('a\x00b')
        with self.assertRaises(ValueError):sse_frame('\u2603',max_bytes=9)
        for limit in (0,-1,True,1048577):
            with self.assertRaises(ValueError):StreamingResponse(200,[],max_chunk_bytes=limit)
        with self.assertRaises(TypeError):StreamingResponse(200,b'not iterable chunks')


if __name__=='__main__':unittest.main()
