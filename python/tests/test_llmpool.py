import unittest
from artex.llmpool import Pool, Member, Registry, State, ExhaustedError, new, estimate_tokens, rotate_groups
from artex.failover import ContextCancelledError


class MockProvider:
    def __init__(self, events=(), result='ok', failure=None):
        self.events, self.result, self.failure = events, result, failure
        self.calls = 0
        self.closed = False

    def complete(self, context, request):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.result

    def stream(self, context, request):
        self.calls += 1
        try:
            yield from self.events
            if self.failure:
                raise self.failure
        finally:
            self.closed = True


def member(id, provider=None, **kw):
    return Member(id, str(id), provider or MockProvider(), **kw)


class PoolTests(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(new([]))
        with self.assertRaises(ValueError):
            Pool([])

    def test_complete_failover(self):
        a, b = MockProvider(failure=RuntimeError('status 401')), MockProvider(result=('msg', 'stop', {'tokens': 1}))
        p = Pool([member(1, a), member(2, b)])
        self.assertEqual(p.complete(None, {}), ('msg', 'stop', {'tokens': 1}))
        self.assertTrue(p.health.is_open(1))
        self.assertEqual(b.calls, 1)

    def test_bad_request_and_cancel_do_not_retry(self):
        for error in [RuntimeError('status 400'), ContextCancelledError()]:
            a, b = MockProvider(failure=error), MockProvider()
            p = Pool([member(1, a), member(2, b)])
            with self.assertRaises(type(error)):
                p.complete(None, {})
            self.assertEqual(b.calls, 0)
            self.assertEqual(p.health.get(1).fails, 0)

    def test_context_cancelled(self):
        class Context:
            def err(self): return 'cancelled'
        p = Pool([member(1, MockProvider(failure=RuntimeError('status 500')))])
        with self.assertRaises(RuntimeError):
            p.complete(Context(), {})
        self.assertEqual(p.health.get(1).fails, 0)

    def test_exhaustion_cause(self):
        failure = RuntimeError('status 503')
        p = Pool([member(1, MockProvider(failure=failure))])
        with self.assertRaises(ExhaustedError) as raised:
            p.complete(None, {})
        self.assertIs(raised.exception.__cause__, failure)

    def test_stream_before_first_event_retries(self):
        a, b = MockProvider(failure=RuntimeError('status 429')), MockProvider(events=['hi'])
        p = Pool([member(1, a), member(2, b)])
        self.assertEqual(list(p.stream(None, {})), ['hi'])
        self.assertEqual(p.health.get(1).fails, 1)
        self.assertTrue(a.closed)

    def test_stream_metadata_closes_retry_window(self):
        a, b = MockProvider(events=[None], failure=RuntimeError('status 503')), MockProvider()
        p = Pool([member(1, a), member(2, b)])
        stream = p.stream(None, {})
        self.assertIsNone(next(stream))
        with self.assertRaises(RuntimeError): next(stream)
        self.assertEqual(b.calls, 0)
        self.assertEqual(p.health.get(1).fails, 0)

    def test_consumer_close(self):
        a, b = MockProvider(events=['a', 'b']), MockProvider()
        p = Pool([member(1, a), member(2, b)])
        stream = p.stream(None, {})
        next(stream)
        stream.close()
        self.assertTrue(a.closed)
        self.assertEqual(b.calls, 0)

    def test_empty_stream_is_success(self):
        health = Registry()
        health.trip(1, 'temporary')
        p = Pool([member(1)], health)
        self.assertEqual(list(p.stream(None, {})), [])
        self.assertEqual(health.get(1).fails, 0)

    def test_order_rotation_and_filtering(self):
        h = Registry(clock=lambda: 100)
        p = Pool([member(1, rank=9), member(2, rank=1), member(3, rank=1)], h)
        self.assertEqual([m.id for m in p.order({})], [1, 2, 3])
        self.assertEqual([m.id for m in p.order({})], [1, 3, 2])
        h.restore(1, State(open_until=200))
        self.assertEqual([m.id for m in p.order({})], [2, 3])

    def test_window_and_last_resort(self):
        p = Pool([member(1, window_tokens=1), member(2, window_tokens=100)])
        self.assertEqual([m.id for m in p.order({'system':['x' * 14]})], [2])
        self.assertEqual([m.id for m in p.order({'system':['x' * 1000]})], [1])

    def test_estimator_utf8_nested(self):
        req = {'system':['é'], 'messages':[{'content':[{'text':'abc', 'content':[{'thinking':'abcd', 'input':b'{}'}]}]}], 'tools':[{'name':'x', 'description':'y', 'input_schema':{'type':'object'}}]}
        self.assertEqual(estimate_tokens(req), (2+3+4+2+1+1+120)*2//7)

    def test_rotation_only_contiguous_groups(self):
        chain = [member(1, rank=1), member(2, rank=2), member(3, rank=1)]
        self.assertEqual(rotate_groups(chain, 1), chain)


if __name__ == '__main__':
    unittest.main()
