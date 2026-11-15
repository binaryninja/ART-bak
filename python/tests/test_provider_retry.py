import unittest
from artex.providers import Response,HTTPError,ProtocolError
from artex.providers.http import TransportError,TransportCancelled
from artex.providers.retry import Policy,RetryProvider,RetryTransport,RetryLimitError
from artex.db.profiles import RetryOverride,RetryRule


class Clock:
    def __init__(self): self.now=0; self.waits=[]
    def __call__(self): return self.now
    def sleep(self,seconds): self.waits.append(seconds); self.now+=seconds


class FakeProvider:
    def __init__(self,results): self.results=iter(results); self.calls=0; self.closed=0
    def complete(self,ctx,request):
        self.calls+=1; item=next(self.results)
        if isinstance(item,Exception): raise item
        return item
    def stream(self,ctx,request):
        self.calls+=1
        try:
            for item in next(self.results):
                if isinstance(item,Exception): raise item
                yield item
        finally: self.closed+=1


EMPTY=({'content':[]},'end_turn',{})
OK=({'content':[{'type':'text','text':'ok'}]},'end_turn',{})
META=[{'type':'message_start'},{'type':'message_delta','stop_reason':'end_turn'},{'type':'message_stop'}]


class RetryTests(unittest.TestCase):
    def setUp(self): self.clock=Clock()
    def wrap(self,p,**kw):
        return RetryProvider(p,clock=self.clock,sleep=self.clock.sleep,format=kw.pop('format','openai'),**kw)

    def test_source_default_ladders(self):
        p=Policy()
        self.assertEqual([p.count(k) for k in ('connect','empty','stream')],[3,2,2])
        self.assertEqual([p.delay('connect',i) for i in range(7)],[.5,1,2,4,8,8,8])
        self.assertEqual(p.delay('stream',8),4)
        rules=RetryOverride(RetryRule(-1,200),RetryRule(1,50),RetryRule(-1))
        self.assertEqual(Policy(rules).count('connect'),0)
        self.assertEqual(Policy(rules).delay('empty',9),.05)

    def test_connect_status_cleanup_and_delays(self):
        calls=[]; closed=[]
        def transport(*args):
            calls.append(args)
            return Response(503 if len(calls)<4 else 200,b'',lambda: closed.append(len(calls)))
        result=RetryTransport(transport,clock=self.clock,sleep=self.clock.sleep)(None,'url',{},b'',False)
        self.assertEqual(result.status,200)
        self.assertEqual(len(calls),4); self.assertEqual(len(closed),3)
        self.assertAlmostEqual(self.clock.now,3.5)

    def test_transport_unknown_or_deterministic_no_retry(self):
        for failure in (ProtocolError('bad'),ValueError('policy')):
            calls=[]
            def transport(*args): calls.append(1); raise failure
            with self.assertRaises(type(failure)):
                RetryTransport(transport,clock=self.clock,sleep=self.clock.sleep)(None,'url',{},b'',False)
            self.assertEqual(len(calls),1)
        calls=[]
        def forbidden(*args): calls.append(1); return Response(401,b'')
        self.assertEqual(RetryTransport(forbidden)(None,'url',{},b'',False).status,401)
        self.assertEqual(len(calls),1)

    def test_empty_complete_retries_only_normal_openai(self):
        p=FakeProvider([EMPTY,EMPTY,OK]); self.assertEqual(self.wrap(p).complete(None,{}),OK)
        self.assertEqual(p.calls,3); self.assertAlmostEqual(self.clock.now,1.5)
        for result in (({'content':[]},'max_tokens',{}),({'content':[],'refusal':'no'},'end_turn',{})):
            p=FakeProvider([result]); self.assertEqual(self.wrap(p).complete(None,{}),result)
        p=FakeProvider([EMPTY]); self.assertEqual(self.wrap(p,format='anthropic').complete(None,{}),EMPTY)

    def test_stream_empty_metadata_not_delivered_until_success(self):
        p=FakeProvider([META,META,[{'type':'text_delta','text':'ok'}]+META[1:]])
        events=list(self.wrap(p).stream(None,{}))
        self.assertEqual(events[0]['text'],'ok'); self.assertEqual(p.calls,3); self.assertEqual(p.closed,3)

    def test_no_replay_after_any_delivered_event(self):
        for event in ({'type':'tool_use_start'},{'type':'text_delta','text':'a'},{'type':'custom_metadata'}):
            p=FakeProvider([[event,TransportError('lost')]])
            iterator=self.wrap(p).stream(None,{})
            self.assertEqual(next(iterator),event)
            with self.assertRaises(TransportError): next(iterator)
            self.assertEqual(p.calls,1)
        p=FakeProvider([[{'type':'message_start'},TransportError('lost')]])
        iterator=self.wrap(p,format='anthropic').stream(None,{})
        next(iterator)
        with self.assertRaises(TransportError): next(iterator)
        self.assertEqual(p.calls,1)

    def test_stream_precommit_failure_and_consumer_close(self):
        p=FakeProvider([[TransportError('lost')],[{'type':'text_delta','text':'ok'}]])
        iterator=self.wrap(p).stream(None,{})
        self.assertEqual(next(iterator)['text'],'ok'); iterator.close()
        self.assertEqual(p.calls,2); self.assertEqual(p.closed,2)

    def test_complete_transient_but_not_refusal_or_protocol(self):
        p=FakeProvider([HTTPError(503),OK]); self.assertEqual(self.wrap(p).complete(None,{}),OK)
        for error in (HTTPError(403),ProtocolError('bad')):
            p=FakeProvider([error])
            with self.assertRaises(type(error)): self.wrap(p).complete(None,{})
            self.assertEqual(p.calls,1)

    def test_cancel_before_call_and_during_backoff(self):
        p=FakeProvider([OK])
        with self.assertRaises(TransportCancelled): self.wrap(p).complete({'cancelled':True},{})
        self.assertEqual(p.calls,0)
        context={}
        def sleep(delay): self.clock.sleep(delay); context['cancelled']=True
        p=FakeProvider([TransportError('lost'),OK])
        wrapper=RetryProvider(p,format='openai',clock=self.clock,sleep=sleep)
        with self.assertRaises(TransportCancelled): wrapper.complete(context,{})
        self.assertEqual(p.calls,1)

    def test_deadline_and_aggregate_attempt_limit(self):
        p=FakeProvider([TransportError('lost')]*4)
        with self.assertRaises(RetryLimitError): self.wrap(p,policy=Policy(max_seconds=.1)).complete(None,{})
        self.assertEqual(p.calls,1)
        p=FakeProvider([EMPTY]*4)
        with self.assertRaises(RetryLimitError): self.wrap(p,policy=Policy(max_attempts=2)).complete(None,{})
        self.assertEqual(p.calls,2)

    def test_layers_share_budget(self):
        calls=[]
        def transport(*args): calls.append(1); return Response(503,b'')
        policy=Policy(max_attempts=3)
        t=RetryTransport(transport,policy,clock=self.clock,sleep=self.clock.sleep)
        class P:
            def complete(self,ctx,req):
                response=t(ctx,'url',{},b'',False)
                raise HTTPError(response.status)
            def stream(self,ctx,req): return iter(())
        with self.assertRaises(RetryLimitError): self.wrap(P(),policy=policy).complete(None,{})
        self.assertEqual(len(calls),2) # provider entry + two transport entries

    def test_buffered_refusal_is_sticky_before_delivery(self):
        for reason in ('refusal','refused','content_filter'):
            p=FakeProvider([[{'type':'message_delta','stop_reason':reason},
                             {'type':'message_delta','stop_reason':'end_turn'},
                             TransportError('lost')]])
            with self.assertRaises(TransportError): list(self.wrap(p).stream(None,{}))
            self.assertEqual(p.calls,1)
        p=FakeProvider([[{'type':'message_delta','stop_reason':'refusal'}]+META[1:]])
        self.assertEqual(len(list(self.wrap(p).stream(None,{}))),3)
        self.assertEqual(p.calls,1)

    def test_metadata_bound_and_no_refusal_retry(self):
        p=FakeProvider([[{'type':'message_start'}]*9])
        with self.assertRaises(ValueError): list(self.wrap(p).stream(None,{}))
        p=FakeProvider([[{'type':'message_delta','stop_reason':'refusal'},{'type':'message_stop'}]])
        self.assertEqual(len(list(self.wrap(p).stream(None,{}))),2); self.assertEqual(p.calls,1)


if __name__=='__main__': unittest.main()
