"""Pure OpenAI response/frame fixtures; never constructs a transport or provider."""
from copy import deepcopy
import json
import unittest

from artex.providers import Config, IncompleteStreamError, ProtocolError, ProviderError, _openai_events, parse_response


CONFIG = Config('openai', 'configured')


def response(message, finish='stop', **extra):
    return {'choices': [{'message': message, 'finish_reason': finish}], **extra}


def frame(delta=None, finish=None, **extra):
    return {'choices': [{'delta': delta, 'finish_reason': finish}], **extra}


def call(index=0, identity='a', name='read', arguments='{}', **extra):
    return {'index': index, 'id': identity, 'type': 'function',
            'function': {'name': name, 'arguments': arguments}, **extra}


def events(*frames):
    return list(_openai_events([json.dumps(f) if not isinstance(f, str) else f for f in frames], CONFIG.model))


class OpenAIResponseProtocolTests(unittest.TestCase):
    def test_completion_order_alias_priority_model_usage_and_no_mutation(self):
        value = response({'content': 'answer', 'reasoning_content': 'think',
                          'reasoning': 'duplicate', 'reasoning_text': 'last',
                          'tool_calls': [call(arguments='{"x":[1]}')]}, 'tool_calls',
                         model='returned', usage={'prompt_tokens': 9, 'completion_tokens': 4,
                                                  'prompt_tokens_details': {'cached_tokens': 3}})
        saved = deepcopy(value)
        message, stop, usage = parse_response(CONFIG, value)
        self.assertEqual(message, {'role': 'assistant', 'model': 'returned', 'content': [
            {'type': 'thinking', 'thinking': 'think'}, {'type': 'text', 'text': 'answer'},
            {'type': 'tool_use', 'id': 'a', 'name': 'read', 'input': {'x': [1]}}]})
        self.assertEqual(stop, 'tool_use')
        self.assertEqual(usage, dict(input_tokens=9, output_tokens=4, cache_read_tokens=3, cache_write_tokens=0))
        message['content'][2]['input']['x'].append(2)
        self.assertEqual(value, saved)

    def test_completion_alias_fallback_and_empty_values(self):
        for alias in ('reasoning_content', 'reasoning', 'reasoning_text'):
            with self.subTest(alias=alias):
                message = {'content': None, 'refusal': '', 'reasoning_content': '', 'reasoning': None, alias: 'thought'}
                result, _, _ = parse_response(CONFIG, response(message))
                self.assertEqual(result['content'], [{'type': 'thinking', 'thinking': 'thought'}])
        self.assertEqual(parse_response(CONFIG, response({'content': '', 'tool_calls': None}))[0]['content'], [])

    def test_completion_refusal_fallback_and_priority(self):
        for content, expected in ((None, 'declined'), ('', 'declined'), ('answer', 'answer')):
            with self.subTest(content=content):
                result, stop, _ = parse_response(CONFIG, response({'content': content, 'refusal': 'declined'}, 'tool_calls'))
                self.assertEqual(result['content'], [{'type': 'text', 'text': expected}])
                self.assertEqual(result['refusal'], 'declined')
                self.assertEqual(stop, 'refusal')

    def test_completion_go_whitespace_only_arguments_are_empty_object(self):
        spaces = '\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
        for arguments in (None, '', spaces, *spaces):
            with self.subTest(arguments=repr(arguments)):
                result, _, _ = parse_response(CONFIG, response({'tool_calls': [call(arguments=arguments)]}))
                self.assertEqual(result['content'][0]['input'], {})

    def test_completion_keeps_object_argument_extension(self):
        result, _, _ = parse_response(CONFIG, response({'tool_calls': [call(arguments={'x': True})]}))
        self.assertEqual(result['content'][0]['input'], {'x': True})

    def test_completion_rejects_non_object_or_invalid_json_arguments(self):
        for arguments in ('[]', '1', 'null', 'false', '{', '\x1c', '\x1d', '\x1e', '\x1f', '\u200b', [], 1, False):
            with self.subTest(arguments=repr(arguments)), self.assertRaises(ProtocolError):
                parse_response(CONFIG, response({'tool_calls': [call(arguments=arguments)]}))

    def test_completion_text_fields_require_nullable_strings_even_when_falsy(self):
        for key in ('content', 'refusal', 'reasoning_content', 'reasoning', 'reasoning_text'):
            for value in (False, 0, [], {}, ['x'], {'text': 'x'}):
                with self.subTest(key=key, value=value), self.assertRaises(ProtocolError):
                    parse_response(CONFIG, response({key: value}))

    def test_completion_nonselected_alias_and_refusal_are_still_validated(self):
        for message in ({'reasoning_content': 'valid', 'reasoning': []},
                        {'content': 'valid', 'refusal': False}, {'content': [], 'refusal': 'declined'}):
            with self.subTest(message=message), self.assertRaises(ProtocolError):
                parse_response(CONFIG, response(message))

    def test_completion_malformed_envelopes_raise_protocol_error(self):
        for value in ({'choices': {}}, {'choices': 'x'}, {'choices': [1]}, {'choices': [[]]},
                      response(False), response([]), response('x'), response({}, finish=[])):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_response(CONFIG, value)

    def test_completion_malformed_tools_raise_protocol_error(self):
        bad = [False, 0, {}, 'x', [None], [[]], [{}], [call(identity=None)], [call(identity=1)],
               [call(name='')], [call(name=[])], [dict(call(), function=False)],
               [call(type='custom')], [call(type=False)], [call(type={})]]
        for calls in bad:
            with self.subTest(calls=calls), self.assertRaises(ProtocolError):
                parse_response(CONFIG, response({'tool_calls': calls}))

    def test_completion_first_choice_unknown_metadata_and_finish_mapping(self):
        for finish, expected in (('stop', 'end_turn'), ('length', 'max_tokens'), ('tool_calls', 'tool_use'),
                                 ('content_filter', 'content_filter'), (None, '')):
            value = response({'content': 'first', 'annotation': {'unknown': 1}}, finish)
            value['choices'].append({'message': {'content': 'second'}})
            message, stop, _ = parse_response(CONFIG, value)
            self.assertEqual(message['content'][0]['text'], 'first')
            self.assertEqual(stop, expected)


class OpenAIStreamProtocolTests(unittest.TestCase):
    def test_stream_content_and_refusal_both_preserved_in_order(self):
        result = events(frame({'reasoning': 'think', 'content': 'answer', 'refusal': 'declined'}),
                        frame({}, 'tool_calls'), '[DONE]')
        self.assertEqual(result[1:4], [{'type': 'thinking_delta', 'text': 'think'},
                                      {'type': 'text_delta', 'text': 'answer'},
                                      {'type': 'text_delta', 'text': 'declined'}])
        self.assertEqual(result[-2]['stop_reason'], 'refusal')

    def test_stream_parallel_calls_and_delayed_identity_preserve_fragments(self):
        result = events(frame({'tool_calls': [call(index=2, identity=None, name=None, arguments='{'),
                                             call(index=7, identity='b', name='write', arguments='[')]}),
                        frame({'tool_calls': [call(index=2, arguments='"x":1}'),
                                             call(index=7, identity=None, name=None, arguments=']')]}), '[DONE]')
        self.assertEqual([(e['tool_id'], e['tool_name']) for e in result if e['type'] == 'tool_use_start'], [('b', 'write'), ('a', 'read')])
        self.assertEqual([(e['index'], e['tool_id'], e['text']) for e in result if e['type'] == 'tool_input_delta'],
                         [(7, 'b', '['), (2, 'a', '{'), (2, 'a', '"x":1}'), (7, 'b', ']')])

    def test_stream_repeated_identical_identity_is_allowed(self):
        result = events(frame({'tool_calls': [call(arguments='{')]}), frame({'tool_calls': [call(arguments='}')]}), '[DONE]')
        self.assertEqual(sum(e['type'] == 'tool_use_start' for e in result), 1)
        self.assertEqual([e['tool_id'] for e in result if e['type'] == 'tool_input_delta'], ['a', 'a'])

    def test_stream_changed_identity_is_rejected_before_mislabeled_fragment(self):
        iterator = _openai_events([frame({'tool_calls': [call(arguments='{')]}),
                                  frame({'tool_calls': [call(identity='b', arguments='}')]}), '[DONE]'], CONFIG.model)
        self.assertEqual(next(iterator)['type'], 'message_start')
        self.assertEqual(next(iterator)['tool_id'], 'a')
        self.assertEqual(next(iterator)['tool_id'], 'a')
        with self.assertRaises(ProtocolError):
            list(iterator)

    def test_stream_changed_identity_before_start_is_rejected(self):
        with self.assertRaises(ProtocolError):
            events(frame({'tool_calls': [call(name=None, arguments='{')]}),
                   frame({'tool_calls': [call(identity='b', arguments='}')]}), '[DONE]')

    def test_stream_text_fields_require_nullable_strings_even_when_falsy(self):
        for key in ('content', 'refusal', 'reasoning_content', 'reasoning', 'reasoning_text'):
            for value in (False, 0, [], {}, ['x'], {'text': 'x'}):
                with self.subTest(key=key, value=value), self.assertRaises(ProtocolError):
                    events(frame({key: value}), '[DONE]')

    def test_stream_nonselected_text_fields_still_validated(self):
        for delta in ({'reasoning_content': 'valid', 'reasoning': []}, {'content': 'valid', 'refusal': False}):
            with self.subTest(delta=delta), self.assertRaises(ProtocolError):
                events(frame(delta), '[DONE]')

    def test_stream_malformed_envelopes_raise_protocol_error(self):
        for value in ({'choices': {}}, {'choices': 'x'}, {'choices': [1]}, {'choices': [[]]},
                      frame(False), frame([]), frame('x'), frame({}, finish=[])):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                events(value, '[DONE]')

    def test_stream_malformed_tools_raise_protocol_error(self):
        bad = [False, 0, {}, 'x', [None], [[]], [dict(call(), function=False)],
               [call(identity=1)], [call(name=[])], [call(arguments={})], [call(arguments=False)],
               [call(type='custom')], [call(type=False)], [call(index=-1)], [call(index=False)],
               [call(index='0')], [call(index=[])], [call(index=1.0)]]
        for calls in bad:
            with self.subTest(calls=calls), self.assertRaises(ProtocolError):
                events(frame({'tool_calls': calls}), '[DONE]')

    def test_stream_nullable_fields_default_index_and_partial_arguments(self):
        tool = {'id': 'a', 'function': {'name': 'read', 'arguments': '{'}}
        result = events(frame({'content': None, 'refusal': None, 'reasoning': None, 'tool_calls': None}),
                        frame({'tool_calls': [tool]}, 'length'), '[DONE]')
        self.assertEqual(result[1]['index'], 0)
        self.assertEqual(result[2]['text'], '{')
        self.assertEqual(result[-2]['stop_reason'], 'max_tokens')

    def test_stream_usage_snapshot_and_alias_mapping_unchanged(self):
        result = events(frame({'reasoning_content': '', 'reasoning': 'preferred', 'reasoning_text': 'ignored'}, model='returned'),
                        {'choices': [], 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}},
                        {'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 5, 'prompt_tokens_details': {'cached_tokens': 2}}}, '[DONE]')
        self.assertEqual(result[0], {'type': 'message_start', 'model': 'returned', 'usage': dict(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)})
        self.assertEqual(result[1], {'type': 'thinking_delta', 'text': 'preferred'})
        self.assertEqual(result[-2]['usage'], dict(input_tokens=7, output_tokens=5, cache_read_tokens=2, cache_write_tokens=0))

    def test_stream_first_choice_unknown_metadata_and_finish_mapping(self):
        for finish, expected in (('stop', 'end_turn'), ('tool_calls', 'tool_use'), ('content_filter', 'content_filter')):
            value = frame({'content': 'first', 'annotation': {}}, finish)
            value['choices'].append({'delta': {'content': 'second'}})
            result = events(value, '[DONE]')
            self.assertEqual(result[1], {'type': 'text_delta', 'text': 'first'})
            self.assertEqual(result[-2]['stop_reason'], expected)

    def test_stream_errors_incomplete_identity_and_terminal_ownership(self):
        with self.assertRaises(ProviderError):
            events({'error': {'type': 'synthetic'}})
        with self.assertRaises(ProtocolError):
            events(frame({'tool_calls': [call(identity=None)]}), '[DONE]')
        with self.assertRaises(IncompleteStreamError):
            events(frame({'content': 'partial'}))
        with self.assertRaises(ProtocolError):
            events(frame({'tool_calls': [call()]}), frame({'tool_calls': [call(name='different')]}), '[DONE]')
        def source():
            yield frame({'content': 'text'})
            yield '[DONE]'
            raise AssertionError('read after terminal')
        self.assertEqual(list(_openai_events(source(), CONFIG.model))[-1], {'type': 'message_stop'})


if __name__ == '__main__':
    unittest.main()
