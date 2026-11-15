"""Independent retry review: inert providers/transports, no network."""
import unittest
from artex.providers import Response,HTTPError
from artex.providers.http import TransportError,TransportCancelled
from artex.providers.retry import RetryProvider,RetryTransport,Policy,RetryLimitError
from test_provider_retry import Clock,FakeProvider,OK,META


class RetryReviewTests(unittest.TestCase):
    def setUp(self):self.clock=Clock()
    def wrapper(self,p):return RetryProvider(p,format='openai',clock=self.clock,sleep=self.clock.sleep)
    def test_refusal_metadata_then_failure_never_replays(self):
        refusal={'type':'message_delta','stop_reason':'refusal'}
        provider=FakeProvider([[refusal,TransportError('lost')],[{'type':'text_delta','text':'unexpected replay'}]])
        with self.assertRaises(TransportError):list(self.wrapper(provider).stream(None,{}))
        self.assertEqual(provider.calls,1)
    def test_delivery_after_buffered_metadata_commits(self):
        provider=FakeProvider([[{'type':'message_start'},{'type':'text_delta','text':'ok'},TransportError('lost')]])
        stream=self.wrapper(provider).stream(None,{})
        self.assertEqual(next(stream)['type'],'message_start')
        self.assertEqual(next(stream)['text'],'ok')
        with self.assertRaises(TransportError):next(stream)
        self.assertEqual(provider.calls,1);self.assertEqual(provider.closed,1)
    def test_cancel_after_response_closes_it_before_return(self):
        context={};closed=[]
        def transport(*args):
            context['cancelled']=True
            return Response(200,b'',lambda:closed.append(True))
        wrapped=RetryTransport(transport,clock=self.clock,sleep=self.clock.sleep)
        with self.assertRaises(TransportCancelled):wrapped(context,'https://invalid.example',{},b'',False)
        self.assertEqual(closed,[True])
    def test_consumer_close_after_first_buffered_event_closes_provider(self):
        provider=FakeProvider([[{'type':'message_start'},{'type':'text_delta','text':'ok'}]])
        stream=self.wrapper(provider).stream(None,{})
        next(stream);stream.close()
        self.assertEqual(provider.calls,1);self.assertEqual(provider.closed,1)
    def test_http_error_does_not_expose_request_secret(self):
        calls=[]
        def transport(ctx,url,headers,body,stream):
            calls.append(1);return Response(401,b'secret-response-body')
        wrapped=RetryTransport(transport)
        response=wrapped(None,'https://invalid.example',{'Authorization':'secret-key'},b'secret-prompt',False)
        error=HTTPError(response.status)
        self.assertEqual(str(error),'provider HTTP status 401')
        self.assertEqual(calls,[1])
    def test_shared_nested_attempt_bound(self):
        calls=[];policy=Policy(max_attempts=2)
        def transport(*args):calls.append(1);raise TransportError('lost')
        inner=RetryTransport(transport,policy,clock=self.clock,sleep=self.clock.sleep)
        class Provider:
            def complete(self,ctx,request):return inner(ctx,'https://invalid.example',{},b'',False)
            def stream(self,ctx,request):return iter(())
        outer=RetryProvider(Provider(),policy,format='openai',clock=self.clock,sleep=self.clock.sleep)
        with self.assertRaises(RetryLimitError):outer.complete(None,{})
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
