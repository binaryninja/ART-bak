"""Buffered protocol contracts; every exchange uses an in-memory transport."""
import json
import unittest
from unittest.mock import patch

from artex.mcphttp import Client, HTTPResponse, MCPError, RPCError, parse_sse


def frame(**fields):
    return {'jsonrpc': '2.0', 'id': 1, **fields}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def reply(value, *, sse=False, headers=None):
    body = encoded(value)
    if sse:
        body = b'data: ' + body + b'\r\n\r\n'
    return HTTPResponse(200, {
        'Content-Type': 'text/event-stream' if sse else 'application/json',
        **(headers or {}),
    }, body)


class FixtureTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class BufferedSSETests(unittest.TestCase):
    def test_unicode_line_separators_are_json_string_data(self):
        # str.splitlines() incorrectly treats these legal JSON characters as
        # physical SSE boundaries, unlike the Go byte scanner.
        for separator in ('\u0085', '\u2028', '\u2029'):
            with self.subTest(separator=repr(separator)):
                value = frame(result={'text': 'before' + separator + 'after'})
                self.assertEqual(parse_sse(b'data: ' + encoded(value) + b'\n\n', 1), value)

    def test_initial_utf8_bom_is_ignored_once(self):
        value = frame(result='ok')
        self.assertEqual(parse_sse(b'\xef\xbb\xbfdata: ' + encoded(value) + b'\n\n', 1), value)
        with self.assertRaises(MCPError):
            parse_sse(b'\xef\xbb\xbf\xef\xbb\xbfdata: ' + encoded(value) + b'\n\n', 1)

    def test_fragmented_data_fields_and_all_sse_line_endings(self):
        for newline in (b'\n', b'\r\n', b'\r'):
            with self.subTest(newline=newline):
                body = newline.join([
                    b': heartbeat', b'event: message', b'id: event-1',
                    b'data: {"jsonrpc":"2.0",', b'data', b'data: "id":1,',
                    b'data: "result":{"content":[',
                    b'data: {"type":"text","text":"ok"}]}}', b'', b'',
                ])
                self.assertEqual(parse_sse(body, 1)['result'], {
                    'content': [{'type': 'text', 'text': 'ok'}],
                })

    def test_mixed_line_endings_and_buffered_eof_flush(self):
        body = b'data: {"jsonrpc":"2.0",\r\ndata: "id":1,\rdata: "result":null}\n'
        self.assertIsNone(parse_sse(body, 1)['result'])
        self.assertEqual(parse_sse(b'data: ' + encoded(frame(result=False)), 1)['result'], False)

    def test_server_requests_with_colliding_id_do_not_finish_the_rpc(self):
        request = frame(method='ping', params={})
        notification = {'jsonrpc': '2.0', 'method': 'notifications/progress'}
        unrelated = {'jsonrpc': '2.0', 'id': 2, 'result': 'other'}
        answer = frame(result='answer')
        body = b''.join(b'data: ' + encoded(value) + b'\n\n'
                        for value in (notification, request, unrelated, answer))
        self.assertEqual(parse_sse(body, 1), answer)
        with self.assertRaises(MCPError):
            parse_sse(b'data: ' + encoded(request) + b'\n\n', 1)

    def test_byte_bound_and_invalid_utf8_still_fail(self):
        body = b'data: ' + encoded(frame(result='ok')) + b'\n\n'
        with patch('artex.mcphttp.MAX_RESPONSE_BYTES', len(body)):
            self.assertEqual(parse_sse(body, 1)['result'], 'ok')
        with patch('artex.mcphttp.MAX_RESPONSE_BYTES', len(body) - 1):
            with self.assertRaisesRegex(MCPError, 'byte limit'):
                parse_sse(body, 1)
        with self.assertRaisesRegex(MCPError, 'invalid UTF-8'):
            parse_sse(b'data: \xff\n\n', 1)

    def test_matching_frame_does_not_concatenate_across_event_boundaries(self):
        body = b'data: {"jsonrpc":"2.0",\n\ndata: "id":1,"result":true}\n\n'
        with self.assertRaises(MCPError):
            parse_sse(body, 1)


class RPCEnvelopeTests(unittest.TestCase):
    def client(self, *responses):
        transport = FixtureTransport(*responses)
        return Client('fixture', 'https://fixture.invalid/mcp', transport=transport), transport

    def test_null_and_false_results_remain_valid(self):
        for sse in (False, True):
            for value in (None, False, 0, '', [], {}):
                with self.subTest(sse=sse, value=value):
                    client, transport = self.client(reply(frame(result=value), sse=sse))
                    self.assertEqual(client.rpc('fixture', {}), value)
                    self.assertEqual(len(transport.requests), 1)

    def test_error_only_envelope_keeps_code_message_and_no_retry(self):
        for sse in (False, True):
            with self.subTest(sse=sse):
                client, transport = self.client(reply(frame(error={
                    'code': -32001, 'message': 'fixture failure', 'data': {'detail': 1},
                }), sse=sse))
                with self.assertRaises(RPCError) as caught:
                    client.rpc('fixture', {})
                self.assertEqual(caught.exception.code, -32001)
                self.assertEqual(caught.exception.message, 'fixture failure')
                self.assertEqual(len(transport.requests), 1)

    def test_result_and_error_presence_are_mutually_exclusive(self):
        for sse in (False, True):
            for value in (
                frame(), frame(error=None), frame(result='ok', error=None),
                frame(result=None, error={'code': -1, 'message': 'failed'}),
            ):
                with self.subTest(sse=sse, value=value):
                    client, transport = self.client(reply(value, sse=sse))
                    with self.assertRaises(MCPError) as caught:
                        client.rpc('fixture', {})
                    self.assertNotIsInstance(caught.exception, RPCError)
                    self.assertEqual(len(transport.requests), 1)

    def test_malformed_error_never_becomes_remote_failure(self):
        for sse in (False, True):
            for error in ([], 'no', {}, {'code': True, 'message': 'no'},
                          {'code': 1.0, 'message': 'no'}, {'code': -1, 'message': None}):
                with self.subTest(sse=sse, error=error):
                    client, _ = self.client(reply(frame(error=error), sse=sse))
                    with self.assertRaises(MCPError) as caught:
                        client.rpc('fixture', {})
                    self.assertNotIsInstance(caught.exception, RPCError)

    def test_request_is_not_a_json_response_even_with_a_result_member(self):
        client, _ = self.client(reply(frame(method='ping', result='not an answer')))
        with self.assertRaises(MCPError):
            client.rpc('fixture', {})

    def test_non_json_numeric_constants_are_rejected_in_both_formats(self):
        for sse in (False, True):
            for constant in (b'NaN', b'Infinity', b'-Infinity'):
                with self.subTest(sse=sse, constant=constant):
                    body = b'{"jsonrpc":"2.0","id":1,"result":' + constant + b'}'
                    headers = {}
                    if sse:
                        body = b'data: ' + body + b'\n\n'
                        headers = {'Content-Type': 'text/event-stream'}
                    client, _ = self.client(HTTPResponse(200, headers, body))
                    with self.assertRaises(MCPError):
                        client.rpc('fixture', {})


class SessionResponseTests(unittest.TestCase):
    def client(self, *responses):
        transport = FixtureTransport(*responses)
        return Client('fixture', 'https://fixture.invalid/mcp', transport=transport), transport

    def test_failed_initialize_does_not_latch_session_or_send_notification(self):
        invalid = (
            b'not JSON', encoded(frame(id=2, result={})), encoded(frame(jsonrpc='1.0', result={})),
            encoded(frame()), encoded(frame(error={'code': -1, 'message': 'failed'})),
            encoded(frame(result={}, error=None)),
        )
        for sse in (False, True):
            for body in invalid:
                with self.subTest(sse=sse, body=body):
                    headers = {'Mcp-Session-Id': 'unaccepted'}
                    if sse:
                        body = b'data: ' + body + b'\n\n'
                        headers['Content-Type'] = 'text/event-stream'
                    client, transport = self.client(HTTPResponse(200, headers, body))
                    with self.assertRaises(MCPError):
                        client.initialize()
                    self.assertEqual(client.session_id, '')
                    client.close()
                    self.assertEqual([r.method for r in transport.requests], ['POST'])

    def test_only_initialize_can_establish_a_session(self):
        client, transport = self.client(
            reply(frame(result={}), headers={'Mcp-Session-Id': 'from-list'}),
            HTTPResponse(202, {'Mcp-Session-Id': 'from-notification'}),
            reply(frame(id=2, result={}), headers={'Mcp-Session-Id': 'initialized'}),
            HTTPResponse(202, {'Mcp-Session-Id': 'replacement'}),
            HTTPResponse(204, {}),
        )
        client.rpc('tools/list', {})
        self.assertEqual(client.session_id, '')
        client.notify('notifications/fixture', {})
        self.assertEqual(client.session_id, '')
        client.initialize()
        self.assertEqual(client.session_id, 'initialized')
        self.assertNotIn('mcp-session-id', transport.requests[2].headers)
        self.assertEqual(transport.requests[3].headers['mcp-session-id'], 'initialized')
        client.close()
        self.assertEqual(transport.requests[-1].headers['mcp-session-id'], 'initialized')

    def test_successful_json_and_sse_initialize_propagate_session(self):
        for sse in (False, True):
            with self.subTest(sse=sse):
                client, transport = self.client(
                    reply(frame(result={}), sse=sse, headers={'MCP-SESSION-ID': 'fixture-session'}),
                    HTTPResponse(202, {}),
                    reply(frame(id=2, result='ok'), headers={'Mcp-Session-Id': 'ignored'}),
                    HTTPResponse(204, {}),
                )
                self.assertEqual(client.initialize(), {})
                self.assertEqual(client.rpc('fixture', {}), 'ok')
                client.close()
                self.assertEqual([r.method for r in transport.requests], ['POST', 'POST', 'POST', 'DELETE'])
                self.assertNotIn('mcp-session-id', transport.requests[0].headers)
                for request in transport.requests[1:]:
                    self.assertEqual(request.headers['mcp-session-id'], 'fixture-session')

    def test_initialized_notification_failure_retains_valid_session_for_close(self):
        client, transport = self.client(
            reply(frame(result={}), headers={'Mcp-Session-Id': 'valid-session'}),
            HTTPResponse(503, {}), HTTPResponse(204, {}),
        )
        with self.assertRaisesRegex(MCPError, 'mcp http 503'):
            client.initialize()
        self.assertEqual(client.session_id, 'valid-session')
        client.close()
        self.assertEqual(transport.requests[-1].method, 'DELETE')


if __name__ == '__main__':
    unittest.main()
