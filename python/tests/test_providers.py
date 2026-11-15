import json
import unittest
from artex.providers import Config, Provider, Response, ProtocolError, HTTPError, IncompleteStreamError, ProviderError, serialize, parse_response, ContextBoundProvider
from artex.llmpool import Pool, Member
from artex.llmrec import Recorder


def sse(*frames):
    return b''.join(b'data: ' + (frame.encode() if isinstance(frame, str) else json.dumps(frame).encode()) + b'\r\n\r\n' for frame in frames)


def request():
    return {'system': [' first ', '', 'second'], 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Hello'}]}]}


class FakeTransport:
    def __init__(self, body, status=200):
        self.body, self.status = body, status
        self.calls, self.closed = [], False
    def __call__(self, context, url, headers, body, stream):
        self.calls.append((context, url, headers, json.loads(body), stream))
        return Response(self.status, self.body, lambda: setattr(self, 'closed', True))


class ProviderTests(unittest.TestCase):
    def test_openai_configuration_and_no_mutation(self):
        r = request()
        r.update(max_tokens=12, temperature=0, stop=['END'], thinking='disabled')
        before = json.dumps(r)
        b = serialize(Config('openai', 'fixture', thinking_type='enabled', reasoning_effort='high', max_tokens_field='max_completion_tokens'), r)
        self.assertEqual(b['max_completion_tokens'], 12)
        self.assertNotIn('max_tokens', b)
        self.assertEqual(b['temperature'], 0)
        self.assertEqual(b['thinking']['type'], 'disabled')
        self.assertEqual(b['messages'][0]['content'], 'first\n\nsecond')
        self.assertNotIn('stream_options', b)
        self.assertEqual(json.dumps(r), before)

    def test_tool_pairing_conversion(self):
        r = {'messages': [
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'a', 'name': 'read', 'input': '{"x":1}'}]},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'Continue'}, {'type': 'tool_result', 'tool_use_id': 'a', 'content': [{'type': 'text', 'text': 'Result'}], 'is_error': True}]}
        ]}
        b = serialize(Config('openai', 'fixture'), r)
        self.assertEqual([m['role'] for m in b['messages']], ['assistant', 'tool', 'user'])
        self.assertEqual(b['messages'][1]['tool_call_id'], 'a')
        self.assertEqual(b['messages'][0]['tool_calls'][0]['function']['arguments'], '{"x":1}')
        b = serialize(Config('anthropic', 'fixture'), r)
        self.assertTrue(b['messages'][1]['content'][1]['is_error'])
        with self.assertRaises(ProtocolError):
            serialize(Config('openai', 'fixture'), {'messages': r['messages'][:1]})
        with self.assertRaises(ProtocolError):
            serialize(Config('openai', 'fixture'), {'messages': r['messages'][1:]})

    def test_anthropic_cache_thinking_and_merge(self):
        r = request()
        r['dynamic_boundary'] = 1
        r['messages'] += [{'role': 'user', 'content': [{'type': 'text', 'text': 'More'}]}, {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'Original', 'signature': 'sig'}, {'type': 'text', 'text': 'Answer'}]}]
        b = serialize(Config('anthropic', 'fixture', thinking_type='disabled'), r)
        self.assertEqual(len(b['messages']), 2)
        self.assertEqual(b['messages'][-1]['content'], [{'type': 'text', 'text': 'Answer'}])
        self.assertEqual(b['system'][0]['cache_control'], {'type': 'ephemeral'})
        self.assertNotIn('cache_control', b['system'][1])
        self.assertEqual(b['max_tokens'], 8192)
        self.assertEqual(serialize(Config('anthropic', 'fixture', thinking_type='enabled'), r)['messages'][-1]['content'][0]['signature'], 'sig')

    def test_completion_refusal_metadata_usage_and_close(self):
        t = FakeTransport(json.dumps({'model': 'returned-model', 'choices': [{'message': {'content': None, 'refusal': 'Cannot help'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 3, 'prompt_tokens_details': {'cached_tokens': 4}}}))
        p = Provider(Config('openai', 'fixture', api_key='test-only'), t)
        message, stop, usage = p.complete({}, request())
        self.assertEqual(message['content'][0]['text'], 'Cannot help')
        self.assertEqual(message['model'], 'returned-model')
        self.assertEqual(stop, 'end_turn')
        self.assertEqual((usage['input_tokens'], usage['cache_read_tokens']), (12, 4))
        self.assertTrue(t.closed)
        self.assertFalse(t.calls[0][-1])
        self.assertEqual(t.calls[0][2]['authorization'], 'Bearer test-only')

    def test_anthropic_completion_and_usage(self):
        msg, stop, usage = parse_response(Config('anthropic', 'fixture'), {'content': [{'type': 'tool_use', 'id': 'a', 'name': 'test', 'input': {'x': 1}}], 'stop_reason': 'tool_use', 'usage': {'input_tokens': 3, 'cache_read_input_tokens': 4, 'cache_creation_input_tokens': 5, 'output_tokens': 2}})
        self.assertEqual(msg['content'][0]['input'], {'x': 1})
        self.assertEqual(stop, 'tool_use')
        self.assertEqual(usage['input_tokens'], 12)

    def test_openai_stream_split_chunks_tool_ids_and_usage(self):
        body = sse({'model': 'fixture', 'choices': [{'delta': {'role': 'assistant'}}]},
                   {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'a', 'function': {'name': 'read', 'arguments': '{'}}]}}]},
                   {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"x":1}'}}]}, 'finish_reason': 'tool_calls'}]},
                   {'choices': [], 'usage': {'prompt_tokens': 8, 'completion_tokens': 3}}, '[DONE]')
        t = FakeTransport([body[i:i+3] for i in range(0, len(body), 3)])
        events = list(Provider(Config('openai', 'fixture'), t).stream(None, request()))
        self.assertEqual([e['type'] for e in events], ['message_start', 'tool_use_start', 'tool_input_delta', 'tool_input_delta', 'message_delta', 'message_stop'])
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'tool_input_delta'), '{"x":1}')
        self.assertEqual(events[-2]['usage']['input_tokens'], 8)
        self.assertEqual(events[2]['tool_id'], 'a')
        self.assertTrue(t.closed)

    def test_anthropic_stream_no_double_count(self):
        body = sse({'type': 'message_start', 'message': {'usage': {'input_tokens': 3, 'cache_read_input_tokens': 4, 'output_tokens': 1}}},
                   {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'Hello'}},
                   {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'input_tokens': 3, 'cache_read_input_tokens': 4, 'output_tokens': 6}},
                   {'type': 'message_stop'})
        events = list(Provider(Config('anthropic', 'fixture'), FakeTransport(body)).stream({}, request()))
        self.assertEqual(sum(e.get('usage', {}).get('input_tokens', 0) for e in events), 7)
        self.assertEqual(sum(e.get('usage', {}).get('output_tokens', 0) for e in events), 6)

    def test_incomplete_stream_error_and_cleanup(self):
        t = FakeTransport(sse({'choices': [{'delta': {'content': 'partial'}}]}))
        it = Provider(Config('openai', 'fixture'), t).stream({}, request())
        self.assertEqual(next(it)['type'], 'message_start')
        self.assertEqual(next(it)['type'], 'text_delta')
        with self.assertRaises(IncompleteStreamError):
            next(it)
        self.assertTrue(t.closed)

    def test_http_error_and_cancellation_do_not_leak(self):
        t = FakeTransport(b'secret', 429)
        with self.assertRaisesRegex(HTTPError, '429') as caught:
            Provider(Config('openai', 'fixture', api_key='secret'), t).complete({}, request())
        self.assertNotIn('secret', str(caught.exception))
        self.assertTrue(t.closed)
        t = FakeTransport(b'')
        with self.assertRaisesRegex(RuntimeError, 'canceled'):
            Provider(Config('openai', 'fixture'), t).complete({'cancelled': True}, request())
        self.assertFalse(t.calls)

    def test_protocol_error_is_not_success(self):
        for payload in ('not JSON', {'choices': []}, {'error': {'type': 'invalid_request_error'}}):
            with self.assertRaises((ProtocolError, ProviderError)):
                parse_response(Config('openai', 'fixture'), payload)
        with self.assertRaises(ProtocolError):
            Provider(Config('openai-responses', 'fixture'), FakeTransport(b''))

    def test_pool_recorder_completion_integration(self):
        class Sink:
            def __init__(self): self.records, self.usage = [], []
            def insert_llm_record(self, row): self.records.append(row)
            def insert_llm_usage(self, row): self.usage.append(row)
        t = FakeTransport(json.dumps({'choices': [{'message': {'content': 'Answer'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 3}}))
        sink = Sink()
        provider = Provider(Config('openai', 'fixture'), t)
        pool = Pool([Member(1, 'fixture', provider)])
        recorder = Recorder(pool, sink, model='fixture')
        result = recorder.complete({'session_id': 'exp2-worker-1'}, request())
        self.assertEqual(result[0]['content'][0]['text'], 'Answer')
        self.assertEqual(sink.usage[0]['output_tokens'], 3)
        self.assertEqual(json.loads(sink.records[0]['response_body'])['text'], 'Answer')

    def test_pool_recorder_streaming_integration(self):
        class Sink:
            def __init__(self): self.records, self.usage = [], []
            def insert_llm_record(self, row): self.records.append(row)
            def insert_llm_usage(self, row): self.usage.append(row)
        body = sse({'choices': [{'delta': {'content': 'Hello'}}]},
                   {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 5, 'completion_tokens': 2}}, '[DONE]')
        sink = Sink()
        pool = Pool([Member(1, 'test', Provider(Config('openai', 'fixture'), FakeTransport(body)))])
        recorder = Recorder(pool, sink, model='fixture')
        list(recorder.stream({}, request()))
        self.assertEqual(sink.usage[0]['input_tokens'], 5)
        self.assertEqual(sink.usage[0]['output_tokens'], 2)
        self.assertEqual(json.loads(sink.records[0]['response_body'])['text'], 'Hello')

    def test_failover_before_any_event_and_bound_contract(self):
        first = FakeTransport(b'not logged', 503)
        fallback = FakeTransport(json.dumps({'choices': [{'message': {'content': 'Fallback'}, 'finish_reason': 'stop'}]}))
        pool = Pool([Member(1, 'first', Provider(Config('openai', 'one'), first)),
                     Member(2, 'second', Provider(Config('openai', 'two'), fallback))])
        bound = ContextBoundProvider(pool, lambda: {'session_id': 'fixture'})
        self.assertEqual(bound.complete(request())[0]['content'][0]['text'], 'Fallback')
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(fallback.calls), 1)

    def test_explicit_close_releases_transport(self):
        t = FakeTransport(sse({'choices': [{'delta': {'content': 'one'}}]}, '[DONE]'))
        it = Provider(Config('openai', 'fixture'), t).stream({}, request())
        next(it)
        it.close()
        self.assertTrue(t.closed)

    def test_unknown_blocks_and_duplicate_results_fail_closed(self):
        with self.assertRaises(ProtocolError):
            serialize(Config('openai', 'fixture'), {'messages': [{'role': 'user', 'content': [{'type': 'image'}]}]})
        call = {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'a', 'name': 'read', 'input': {}}]}
        result = {'type': 'tool_result', 'tool_use_id': 'a', 'content': []}
        with self.assertRaises(ProtocolError):
            serialize(Config('openai', 'fixture'), {'messages': [call, {'role': 'user', 'content': [result, result]}]})

    def test_pool_never_failsover_after_stream_event(self):
        first = FakeTransport(sse({'choices': [{'delta': {'content': 'partial'}}]}))
        fallback = FakeTransport(b'')
        pool = Pool([Member(1, 'first', Provider(Config('openai', 'first'), first)), Member(2, 'fallback', Provider(Config('openai', 'fallback'), fallback))])
        with self.assertRaises(IncompleteStreamError):
            list(pool.stream({}, request()))
        self.assertFalse(fallback.calls)


if __name__ == '__main__':
    unittest.main()
