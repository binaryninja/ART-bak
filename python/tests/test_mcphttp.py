import json
import unittest
from artex.mcphttp import (Client, HTTPResponse, MCPError, RPCError, ToolError,
    normalize_headers, parse_sse, resolve_sse_endpoint, tool_name)


class MockTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def response(result=None, id=1, **kwargs):
    payload = {'jsonrpc': '2.0', 'id': id}
    if 'error' not in kwargs:
        payload['result'] = result
    payload.update(kwargs)
    return HTTPResponse(200, {}, json.dumps(payload).encode())


class MCPTests(unittest.TestCase):
    def client(self, *responses):
        transport = MockTransport(*responses)
        return Client('demo', 'https://example.invalid/mcp', transport=transport), transport

    def test_names_and_headers(self):
        self.assertEqual(tool_name('a', 'b'), 'mcp__a__b')
        self.assertEqual(normalize_headers({' Authorization ': 'Bearer x'}), {'authorization': 'Bearer x'})
        for headers in ({'bad name': 'x'}, {'X': 'x\r\ny'}, {'X': '1', 'x': '2'}):
            with self.assertRaises(ValueError): normalize_headers(headers)

    def test_initialize_session_notification_and_close(self):
        c, t = self.client(HTTPResponse(200, {'Mcp-Session-Id': 'sid'},
            b'{"jsonrpc":"2.0","id":1,"result":{}}'), HTTPResponse(202, {}), HTTPResponse(204, {}))
        self.assertEqual(c.initialize(), {})
        first, second = t.requests
        self.assertEqual(json.loads(first.body)['params']['protocolVersion'], '2025-06-18')
        self.assertNotIn('id', json.loads(second.body))
        self.assertEqual(second.headers['mcp-session-id'], 'sid')
        c.close(); c.close()
        self.assertEqual(t.requests[-1].method, 'DELETE')
        self.assertEqual(len(t.requests), 3)
        with self.assertRaises(MCPError): c.rpc('x', {})

    def test_tool_metadata_and_call(self):
        c, t = self.client(response({'tools': [{'name': 'echo'}]}),
            response({'content': [{'text': 'a'}, {'type': 'image'}, {'text': 'b'}]}, id=2))
        tool = c.tools()[0]
        self.assertEqual(tool.input_schema, {'type': 'object'})
        self.assertEqual(tool.permission_prompt('demo'), 'call MCP tool mcp__demo__echo?')
        self.assertEqual(c.call('echo', {'a': 1}), 'ab')
        self.assertEqual(json.loads(t.requests[1].body)['params'], {'name': 'echo', 'arguments': {'a': 1}})

    def test_error_contracts(self):
        c, _ = self.client(response(error={'code': -1, 'message': 'no'}))
        with self.assertRaises(RPCError) as err: c.rpc('x', {})
        self.assertEqual(err.exception.code, -1)
        c, _ = self.client(response({'content': [{'text': 'bad'}], 'isError': True}))
        with self.assertRaises(ToolError) as err: c.call('x', None)
        self.assertEqual(err.exception.text, 'bad')

    def test_malformed_and_mismatched(self):
        for reply in (response(id=2), response(id=True), HTTPResponse(200, {}, b'[]'),
                      HTTPResponse(200, {}, b'garbage'), response(error=[])):
            c, _ = self.client(reply)
            with self.assertRaises(MCPError): c.rpc('x', {})

    def test_http_error_does_not_echo_secrets_or_accept_session(self):
        c, _ = self.client(HTTPResponse(401, {'Mcp-Session-Id': 'bad'}, b'secret'))
        with self.assertRaises(MCPError) as err: c.rpc('x', {})
        self.assertNotIn('secret', str(err.exception))
        self.assertEqual(c.session_id, '')

    def test_sse(self):
        body = b':comment\n\ndata: {"id":9}\n\ndata: nope\n\ndata: {"jsonrpc":"2.0",\ndata: "id":1,"result":{"x":2}}'
        self.assertEqual(parse_sse(body, 1)['result'], {'x': 2})
        c, _ = self.client(HTTPResponse(200, {'Content-Type': 'text/event-stream; charset=utf-8'}, body))
        self.assertEqual(c.rpc('x', {}), {'x': 2})
        with self.assertRaises(MCPError): parse_sse(body, 2)

    def test_endpoint_routing(self):
        self.assertEqual(resolve_sse_endpoint('https://host/sse', '/messages?q=1'), 'https://host/messages?q=1')
        for target in ('https://evil/x', '//evil/x', 'http://host/x', 'https://u:p@host/x', '#frag'):
            with self.assertRaises(ValueError): resolve_sse_endpoint('https://host/sse', target)

    def test_reserved_headers(self):
        with self.assertRaises(ValueError):
            Client('x', 'https://host/mcp', transport=MockTransport(), headers={'MCP-Session-ID': 'x'})

    def test_metadata_validation(self):
        for value in (None, {'tools': [1]}, {'tools': [{'name': 'x', 'inputSchema': []}]}):
            c, _ = self.client(response(value))
            with self.assertRaises(MCPError): c.tools()

    def test_schema_empty_is_preserved(self):
        c, _ = self.client(response({'tools': [{'name': 'x', 'inputSchema': {}}]}))
        self.assertEqual(c.tools()[0].input_schema, {})

    def test_no_implicit_retry(self):
        c, t = self.client(HTTPResponse(503, {}))
        with self.assertRaises(MCPError): c.call('x', {})
        self.assertEqual(len(t.requests), 1)
