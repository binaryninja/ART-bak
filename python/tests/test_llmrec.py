import io
import json
import unittest
from artex.llmrec import Capture, Recorder, Usage, capture_from, new_capture, parse_session, task_id_from, with_task_id, with_worker


class Sink:
    def __init__(self):
        self.usage, self.records = [], []
    def insert_llm_usage(self, row):
        self.usage.append(row)
    def insert_llm_record(self, row):
        self.records.append(row)


class Provider:
    def __init__(self):
        self.closed = False
    def stream(self, ctx, req):
        try:
            capture = capture_from(ctx)
            if capture:
                capture.set_request(b'wire request')
                capture.tee_response(200, io.BytesIO(b'wire response')).read()
            yield {'type': 'message_start', 'usage': {'input_tokens': 12, 'cache_read_tokens': 2}}
            yield {'type': 'text_delta', 'text': 'hello'}
            yield {'type': 'thinking_delta', 'text': 'reason'}
            yield {'type': 'message_delta', 'usage': {'output_tokens': 3}, 'stop_reason': 'end'}
        finally:
            self.closed = True
    def complete(self, ctx, req):
        return {'content': [{'type': 'text', 'text': 'answer'}, {'type': 'thinking', 'thinking': 'reason'}]}, 'end', Usage(10, 2)


class RecorderTests(unittest.TestCase):
    def test_context_and_sessions(self):
        original = {'session_id': 'exp12-worker-i3'}
        ctx = with_worker(with_task_id(original, ' registry '), ' judge ')
        self.assertEqual(task_id_from(ctx), 'registry')
        self.assertNotIn('task_id', original)
        self.assertEqual(parse_session(original['session_id']), ('12', 'worker'))
        self.assertEqual(parse_session('exp3'), ('3', ''))
        self.assertEqual(parse_session('other'), ('', ''))
        self.assertEqual(task_id_from(None), '')

    def test_capture_bytes_and_retries(self):
        c = Capture()
        c.set_request(b'first'); c.set_request(b'second')
        source = c.tee_response(429, io.BytesIO(b'error\xff'))
        self.assertEqual(source.read(2), b'er')
        self.assertEqual(c.raw_response(), b'er')
        source.read(); source.close()
        c.tee_response(200, io.BytesIO(b'ok\n')).read()
        self.assertEqual(c.raw_request(), b'first')
        self.assertEqual(c.attempts()[0].body, b'error\xff')
        self.assertIn(b'HTTP 429', c.raw_response())
        self.assertTrue(c.raw_response().endswith(b'ok\n'))

    def test_capture_context(self):
        ctx, capture = new_capture(None)
        self.assertIs(capture_from(ctx), capture)
        self.assertIsNone(capture_from(None))
        self.assertEqual(capture.raw_response(), b'')
        self.assertIsNone(capture.tee_response(200, None))

    def test_stream_records_once_and_folds_usage(self):
        sink = Sink(); provider = Provider()
        recorder = Recorder(provider, sink, 'model')
        context = with_worker(with_task_id({'session_id': 'exp12-worker-i3'}, 'registry'), 'judge')
        events = list(recorder.stream(context, {}))
        self.assertEqual(len(events), 4)
        self.assertTrue(provider.closed)
        self.assertEqual(len(sink.usage), 1)
        self.assertEqual(len(sink.records), 1)
        row = sink.usage[0]
        self.assertEqual((row['task_id'], row['exploration_id'], row['worker']), ('registry', 12, 'judge'))
        self.assertEqual((row['input_tokens'], row['output_tokens'], row['cache_read_tokens']), (12, 3, 2))
        response = json.loads(sink.records[0]['response_body'])
        self.assertEqual((response['text'], response['thinking'], response['stop_reason']), ('hello', 'reason', 'end'))
        self.assertEqual(sink.records[0]['raw_response'], b'wire response')

    def test_disabled_bodies_still_meter(self):
        sink = Sink()
        recorder = Recorder(Provider(), sink, 'model', enabled=lambda: False)
        recorder.serialize_request = lambda _: self.fail('must not serialize')
        list(recorder.stream({}, {}))
        self.assertEqual(len(sink.usage), 1)
        self.assertFalse(sink.records)

    def test_close_partial_stream_records_once(self):
        sink = Sink(); provider = Provider()
        stream = Recorder(provider, sink, 'model').stream({}, {})
        next(stream); stream.close(); stream.close()
        self.assertTrue(provider.closed)
        self.assertEqual(len(sink.usage), 1)
        self.assertEqual(sink.usage[0]['input_tokens'], 12)

    def test_stream_error_preserves_usage(self):
        class Broken:
            def stream(self, ctx, req):
                yield {'type': 'message_start', 'usage': {'input_tokens': 8}}
                raise RuntimeError('failure')
        sink = Sink()
        with self.assertRaisesRegex(RuntimeError, 'failure'):
            list(Recorder(Broken(), sink).stream({}, {}))
        self.assertEqual(sink.usage[0]['status'], 'error')
        self.assertEqual(sink.usage[0]['input_tokens'], 8)
        self.assertEqual(sink.records[0]['error'], 'failure')

    def test_complete_returns_original_tuple(self):
        sink = Sink(); provider = Provider()
        result = Recorder(provider, sink, 'model').complete({}, {})
        self.assertEqual(result, provider.complete({}, {}))
        self.assertEqual(json.loads(sink.records[0]['response_body'])['text'], 'answer')
        self.assertEqual(sink.usage[0]['output_tokens'], 2)

    def test_complete_error_records(self):
        class Broken:
            def complete(self, ctx, req):
                raise ValueError('bad')
        sink = Sink()
        with self.assertRaises(ValueError):
            Recorder(Broken(), sink, 'model').complete({}, {})
        self.assertEqual(sink.records[0]['status'], 'error')
        self.assertEqual(len(sink.usage), 1)

    def test_request_serialization(self):
        recorder = Recorder(None, thinking_type='enabled', reasoning_effort='high')
        body = json.loads(recorder.serialize_request({'thinking': 'disabled', 'temperature': 0, 'tools': [{'name': 'safe', 'schema': 'large'}], 'stop': ['end']}))
        self.assertEqual(body['thinking'], {'type': 'disabled', 'effort': 'high'})
        self.assertEqual(body['tools'], ['safe'])
        self.assertEqual(body['tools_count'], 1)
        self.assertEqual(body['temperature'], 0)
        self.assertNotIn('schema', str(body))

    def test_sink_failure_not_provider_failure(self):
        class BadSink:
            def insert_llm_usage(self, row):
                raise RuntimeError('offline')
            insert_llm_record = insert_llm_usage
        with self.assertLogs('artex.llmrec', level='ERROR'):
            result = Recorder(Provider(), BadSink(), 'model').complete({}, {})
        self.assertEqual(result[1], 'end')

    def test_nil_sink_and_no_empty_meter(self):
        list(Recorder(Provider()).stream({}, {}))
        class Empty:
            def complete(self, ctx, req):
                return {'content': []}, '', Usage()
        sink = Sink()
        Recorder(Empty(), sink).complete({}, {})
        self.assertEqual(sink.usage, [])
        self.assertEqual(len(sink.records), 1)


if __name__ == '__main__':
    unittest.main()
