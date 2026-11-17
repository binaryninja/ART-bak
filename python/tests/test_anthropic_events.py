"""Offline contract checks for supported Anthropic completion and event parsing."""
from copy import deepcopy
import json
import unittest

from artex.providers import (
    Config, IncompleteStreamError, ProtocolError, ProviderError,
    _anthropic_events, parse_response,
)


CONFIG = Config('anthropic', 'configured-model')
START = {'type': 'message_start', 'message': {'model': 'returned-model', 'usage': {
    'input_tokens': 3, 'cache_read_input_tokens': 4,
    'cache_creation_input_tokens': 5, 'output_tokens': 1,
}}}
END = {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'},
       'usage': {'output_tokens': 8}}
STOP = {'type': 'message_stop'}


def block(index, value):
    return {'type': 'content_block_start', 'index': index, 'content_block': value}


def delta(index, kind, **fields):
    return {'type': 'content_block_delta', 'index': index,
            'delta': {'type': kind, **fields}}


def events(*frames):
    return list(_anthropic_events((json.dumps(frame) for frame in frames), CONFIG.model))


class AnthropicEventsTests(unittest.TestCase):
    def test_completion_preserves_supported_order_and_detaches_input(self):
        payload = {'model': 'returned-model', 'content': [
            {'type': 'thinking', 'thinking': 'reason', 'signature': 'sig'},
            {'type': 'text', 'text': 'before'},
            {'type': 'tool_use', 'id': 'call', 'name': 'read', 'input': {'items': [1]}},
            {'type': 'text', 'text': 'after'},
        ], 'stop_reason': 'tool_use', 'usage': START['message']['usage']}
        before = deepcopy(payload)
        message, stop, usage = parse_response(CONFIG, payload)
        self.assertEqual(message, {'role': 'assistant', 'content': before['content'], 'model': 'returned-model'})
        self.assertEqual(stop, 'tool_use')
        self.assertEqual(usage, {'input_tokens': 12, 'output_tokens': 1,
                                 'cache_read_tokens': 4, 'cache_write_tokens': 5})
        message['content'][2]['input']['items'].append(2)
        self.assertEqual(payload, before)

    def test_completion_tool_input_objects_and_stop_reasons(self):
        for value, expected in ((None, {}), ('', {}), ({}, {}), ('{"x":1}', {'x': 1})):
            with self.subTest(value=value):
                message, _, _ = parse_response(CONFIG, {'content': [
                    {'type': 'tool_use', 'id': 'a', 'name': 'read', 'input': value}]})
                self.assertEqual(message['content'][0]['input'], expected)
        for reason in ('end_turn', 'tool_use', 'max_tokens', 'stop_sequence', 'refusal', 'pause_turn'):
            with self.subTest(reason=reason):
                message, stop, _ = parse_response(CONFIG, {'content': [], 'stop_reason': reason})
                self.assertEqual(stop, reason)
                self.assertEqual(message['model'], CONFIG.model)
        for value in ('[1]', '[', 3):
            with self.subTest(bad_input=value), self.assertRaises(ProtocolError):
                parse_response(CONFIG, {'content': [
                    {'type': 'tool_use', 'id': 'a', 'name': 'read', 'input': value}]})

    def test_completion_rejects_unsupported_blocks(self):
        for value in ({'type': 'redacted_thinking', 'data': 'opaque'},
                      {'type': 'server_tool_use', 'id': 'a', 'name': 'search'},
                      {'type': 'future_block'}, {}, 'not-a-block'):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_response(CONFIG, {'content': [value]})

    def test_supported_stream_projection_and_order(self):
        result = events(START,
            block(0, {'type': 'thinking', 'thinking': '', 'signature': ''}),
            delta(0, 'thinking_delta', thinking='reason'),
            delta(0, 'signature_delta', signature='sig'),
            {'type': 'content_block_stop', 'index': 0},
            block(1, {'type': 'text', 'text': ''}),
            delta(1, 'text_delta', text='hello'),
            {'type': 'content_block_stop', 'index': 1},
            block(2, {'type': 'tool_use', 'id': 'tool-a', 'name': 'read', 'input': {}}),
            delta(2, 'input_json_delta', partial_json='{"x":'),
            delta(2, 'input_json_delta', partial_json='1}'),
            {'type': 'content_block_stop', 'index': 2},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 8}},
            STOP)
        self.assertEqual([event['type'] for event in result], [
            'message_start', 'thinking_delta', 'thinking_signature', 'text_delta',
            'tool_use_start', 'tool_input_delta', 'tool_input_delta', 'message_delta', 'message_stop'])
        self.assertEqual([event['index'] for event in result[1:7]], [0, 0, 1, 2, 2, 2])
        self.assertEqual(result[4], {'type': 'tool_use_start', 'index': 2,
                                    'tool_id': 'tool-a', 'tool_name': 'read'})
        self.assertEqual([event['tool_id'] for event in result[5:7]], ['tool-a', 'tool-a'])
        self.assertEqual(''.join(event['text'] for event in result[5:7]), '{"x":1}')
        self.assertEqual(result[-2]['stop_reason'], 'tool_use')

    def test_tool_fragments_remain_attributable_to_their_index(self):
        result = events(START,
            block(2, {'type': 'tool_use', 'id': 'a', 'name': 'first', 'input': {}}),
            block(7, {'type': 'tool_use', 'id': 'b', 'name': 'second', 'input': {}}),
            delta(7, 'input_json_delta', partial_json='{"b":'),
            delta(2, 'input_json_delta', partial_json='{"a":1}'),
            delta(7, 'input_json_delta', partial_json='2}'), END, STOP)
        fragments = [event for event in result if event['type'] == 'tool_input_delta']
        self.assertEqual([(event['index'], event['tool_id']) for event in fragments],
                         [(7, 'b'), (2, 'a'), (7, 'b')])

    def test_usage_keeps_cumulative_output_contract_and_start_input_once(self):
        result = events(START,
            {'type': 'message_delta', 'delta': {}, 'usage': {'output_tokens': 3}},
            {'type': 'message_delta', 'delta': {}, 'usage': {'output_tokens': 3}},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {
                'input_tokens': 3, 'cache_read_input_tokens': 4,
                'cache_creation_input_tokens': 5, 'output_tokens': 8}}, STOP)
        self.assertEqual([event['usage']['output_tokens'] for event in result[:-1]], [0, 3, 3, 8])
        self.assertEqual(sum(event.get('usage', {}).get('input_tokens', 0) for event in result), 12)
        self.assertEqual(result[0]['usage']['cache_read_tokens'], 4)
        self.assertEqual(result[-2]['usage']['cache_read_tokens'], 0)
        self.assertEqual(result[-2]['usage']['cache_write_tokens'], 0)

    def test_unknown_event_and_delta_types_do_not_hide_later_supported_content(self):
        result = events({'type': 'ping'}, START,
            {'type': 'future_event', 'metadata': {'x': 1}},
            block(0, {'type': 'text', 'text': ''}),
            delta(0, 'future_delta', extra='ignored'),
            delta(0, 'text_delta', text='answer'),
            {'type': 'ping'}, {'type': 'content_block_stop', 'index': 0}, END, STOP)
        self.assertEqual([event['type'] for event in result],
                         ['message_start', 'text_delta', 'message_delta', 'message_stop'])
        self.assertEqual(result[1]['text'], 'answer')

    def test_stream_rejects_unsupported_blocks_before_success(self):
        for value in ({'type': 'redacted_thinking', 'data': 'opaque'},
                      {'type': 'server_tool_use', 'id': 'a', 'name': 'search'},
                      {'type': 'future_block'}, {}, 'not-a-block'):
            with self.subTest(value=value):
                iterator = _anthropic_events(iter((START, block(0, value), END, STOP)), CONFIG.model)
                self.assertEqual(next(iterator)['type'], 'message_start')
                with self.assertRaisesRegex(ProtocolError, 'mapping|Unsupported Anthropic response content'):
                    next(iterator)

    def test_unsupported_block_after_text_cannot_finish_a_partial_success(self):
        iterator = _anthropic_events(iter((START, block(0, {'type': 'text', 'text': ''}),
            delta(0, 'text_delta', text='partial'), {'type': 'content_block_stop', 'index': 0},
            block(1, {'type': 'redacted_thinking', 'data': 'opaque'}), END, STOP)), CONFIG.model)
        self.assertEqual(next(iterator)['type'], 'message_start')
        self.assertEqual(next(iterator)['text'], 'partial')
        with self.assertRaises(ProtocolError):
            list(iterator)

    def test_provider_errors_propagate_before_or_during_stream(self):
        error = {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'private-body'}}
        with self.assertRaisesRegex(ProviderError, 'overloaded_error') as caught:
            parse_response(CONFIG, error)
        self.assertNotIn('private-body', str(caught.exception))
        for prefix in ((), (START,), (START, block(0, {'type': 'text', 'text': ''}),
                                    delta(0, 'text_delta', text='partial'))):
            with self.subTest(prefix=prefix), self.assertRaises(ProviderError) as caught:
                events(*prefix, error, STOP)
            self.assertNotIn('private-body', str(caught.exception))

    def test_invalid_json_and_incomplete_streams_fail(self):
        for raw in ('not-json', '[]', '{'):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                list(_anthropic_events(iter((raw,)), CONFIG.model))
        for frames in ((), (START,), (START, END), (STOP,)):
            with self.subTest(frames=frames), self.assertRaises(IncompleteStreamError):
                events(*frames)
        with self.assertRaisesRegex(ProtocolError, 'Duplicate message_start'):
            events(START, START, END, STOP)

    def test_terminal_stop_does_not_consume_trailing_frames(self):
        def source():
            yield START
            yield END
            yield STOP
            raise AssertionError('consumed after terminal message_stop')
        self.assertEqual(list(_anthropic_events(source(), CONFIG.model))[-1], {'type': 'message_stop'})


if __name__ == '__main__':
    unittest.main()
