"""Offline log SSE tests, virtual heartbeat time and event-controlled readers."""
import io
import json
import threading
import unittest
from artex.server.logs import LogRing,LogService,parse_log
from artex.server.core import Router,Request
from artex.server.wsgi import WSGIApplication


class Clock:
    def __init__(self):self.now=0;self.waits=[]
    def __call__(self):return self.now
    def wait(self,condition,seconds):self.waits.append(seconds);self.now+=seconds


class LogStreamTests(unittest.TestCase):
    def setup_service(self,ring=None):
        self.ring=ring or LogRing()
        self.clock=Clock()
        self.router=Router(lambda r:r.header('Authorization')=='dummy')
        LogService(ring=self.ring,stream_clock=self.clock,stream_wait=self.clock.wait).register(self.router)
        self.app=WSGIApplication(self.router)
        self.headers=[]

    def open(self,query='',method='GET',auth=True):
        env={'REQUEST_METHOD':method,'PATH_INFO':'/api/logs/stream','QUERY_STRING':query,'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='dummy'
        return self.app(env,lambda status,headers:self.headers.append((status,dict(headers))))

    @staticmethod
    def payload(chunk):return json.loads(chunk.decode()[6:].strip())

    def test_protected_and_missing_ring(self):
        self.setup_service()
        self.assertIn(b'Unauthorized',b''.join(self.open(auth=False)))
        self.assertEqual(self.ring.subscriber_count,0)
        r=Router(lambda _:True);LogService().register(r)
        self.assertEqual(r.dispatch(Request('GET','/api/logs/stream')).status,503)

    def test_replay_live_and_headers_no_sse_ids(self):
        self.setup_service()
        self.ring.append(parse_log('first'));self.ring.append(parse_log('second'))
        stream=self.open('since=1')
        self.assertEqual(self.ring.subscriber_count,1)
        self.assertEqual(self.headers[0],('200 OK',{'Content-Type':'text/event-stream','Cache-Control':'no-cache','X-Accel-Buffering':'no','X-Content-Type-Options':'nosniff'}))
        chunk=next(stream);self.assertEqual(self.payload(chunk)['seq'],2)
        self.assertFalse(chunk.startswith(b'id:'))
        self.ring.append(parse_log('third'))
        self.assertEqual(self.payload(next(stream))['text'],'third')
        stream.close();self.assertEqual(self.ring.subscriber_count,0)

    def test_subscribe_before_replay_deduplicates_overlap(self):
        class RaceRing(LogRing):
            def recent(self,since,limit):
                self.append(parse_log('during snapshot'))
                return super().recent(since,limit)
        self.setup_service(RaceRing())
        stream=self.open()
        self.assertEqual(self.payload(next(stream))['seq'],1)
        self.assertEqual(next(stream),b': ping\n\n')  # queued seq1 was deduplicated
        stream.close()

    def test_replay_uses_last_1000_and_does_not_rewind_future_cursor(self):
        self.setup_service()
        for i in range(1005):self.ring.append(parse_log(str(i)))
        stream=self.open()
        self.assertEqual(self.payload(next(stream))['seq'],6)
        stream.close()
        stream=self.open('since=2000')
        self.ring.append(parse_log('not after future cursor'))
        self.assertEqual(next(stream),b': ping\n\n')
        stream.close()

    def test_queue_source_drop_newest_and_bounded(self):
        ring=LogRing();subscription=ring.subscribe()
        for i in range(260):ring.append(parse_log(str(i)))
        self.assertEqual(len(subscription.queue),256);self.assertEqual(subscription.dropped,4)
        self.assertEqual([subscription.poll(0).seq for _ in range(256)],list(range(1,257)))
        self.assertIsNone(subscription.poll(0))
        subscription.close();subscription.close();self.assertEqual(ring.subscriber_count,0)

    def test_virtual_20_second_heartbeat(self):
        self.setup_service();stream=self.open()
        self.assertEqual(next(stream),b': ping\n\n');self.assertEqual(self.clock.now,20)
        self.assertEqual(next(stream),b': ping\n\n');self.assertEqual(self.clock.now,40)
        stream.close()

    def test_head_closes_without_poll(self):
        self.setup_service()
        self.assertEqual(list(self.open(method='HEAD')),[])
        self.assertEqual(self.ring.subscriber_count,0);self.assertEqual(self.clock.waits,[])

    def test_invalid_cursor_no_subscription(self):
        self.setup_service()
        for query in ('since=','since=-1','since=x','since=1&since=2','task=3','since=9223372036854775808'):
            self.open(query)
            self.assertTrue(self.headers[-1][0].startswith('400'))
        self.assertEqual(self.ring.subscriber_count,0)

    def test_ring_shutdown_closes_stream(self):
        self.setup_service();stream=self.open()
        self.ring.close();self.assertEqual(list(stream),[])
        self.assertEqual(self.ring.subscriber_count,0)
        self.open();self.assertTrue(self.headers[-1][0].startswith('503'))

    def test_disconnected_reader_wakes_without_waiting_for_heartbeat(self):
        ring=LogRing();subscription=ring.subscribe()
        entered=threading.Event();done=threading.Event();result=[]
        def wait(condition,seconds):entered.set();condition.wait(seconds)
        subscription.wait=wait
        def reader():
            result.append(subscription.poll(20));done.set()
        thread=threading.Thread(target=reader)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            subscription.close()
            self.assertTrue(done.wait(1));self.assertEqual(result,[None])
        finally:
            subscription.close();thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_oversized_event_terminates_and_unsubscribes_without_leak(self):
        self.setup_service();self.ring.append(parse_log('private-'*10000))
        stream=self.open()
        self.assertEqual(list(stream),[]);self.assertTrue(stream.failed)
        self.assertEqual(self.ring.subscriber_count,0)

    def test_snapshot_failure_unsubscribes(self):
        class BadRing(LogRing):
            def recent(self,*a):raise RuntimeError('secret')
        self.setup_service(BadRing())
        self.assertNotIn(b'secret',b''.join(self.open()))
        self.assertTrue(self.headers[-1][0].startswith('503'));self.assertEqual(self.ring.subscriber_count,0)


if __name__=='__main__':unittest.main()
