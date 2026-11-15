import io
import unittest
from artex.server.core import Router, json_response, Response
from artex.server.wsgi import WSGIApplication


class WSGITests(unittest.TestCase):
    def setUp(self):
        self.router = Router(lambda r: r.header('Authorization') == 'Bearer test')
        self.router.add('GET', '/api/health', lambda r, p: json_response(200, {'ok': True}), public=True)
        self.router.add('POST', '/api/echo', lambda r, p: json_response(200, r.json_object()))
        self.app = WSGIApplication(self.router, max_body_bytes=32)

    def call(self, **updates):
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/health', 'QUERY_STRING': '', 'wsgi.input': io.BytesIO()}
        env.update(updates)
        result = []
        body = b''.join(self.app(env, lambda status, headers: result.append((status, dict(headers)))))
        return result[0][0], result[0][1], body

    def test_public_and_protected(self):
        status, headers, body = self.call()
        self.assertEqual(status, '200 OK')
        self.assertEqual(int(headers['Content-Length']), len(body))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(self.call(PATH_INFO='/api/unported')[0].startswith('401'))
        self.assertTrue(self.call(PATH_INFO='/api/unported', HTTP_AUTHORIZATION='Bearer test')[0].startswith('404'))

    def test_body_and_headers(self):
        status, _, body = self.call(REQUEST_METHOD='POST', PATH_INFO='/api/echo', CONTENT_LENGTH='7',
                                    HTTP_AUTHORIZATION='Bearer test', **{'wsgi.input': io.BytesIO(b'{"x":1}')})
        self.assertEqual(status, '200 OK')
        self.assertEqual(body, b'{"x":1}\n')

    def test_rejects_body_without_reading(self):
        class NeverRead:
            def read(self, n):
                raise AssertionError('must not read')
        for length, status in [('33', '413'), ('-1', '400'), ('1.2', '400'), ('١', '400')]:
            self.assertTrue(self.call(CONTENT_LENGTH=length, **{'wsgi.input': NeverRead()})[0].startswith(status))
        self.assertTrue(self.call(CONTENT_LENGTH='3', **{'wsgi.input': io.BytesIO(b'x')})[0].startswith('400'))

    def test_injection_and_transfer_rejected(self):
        self.assertTrue(self.call(HTTP_AUTHORIZATION='test\r\nInjected: yes')[0].startswith('400'))
        self.assertTrue(self.call(PATH_INFO='/api/health\r\n')[0].startswith('400'))
        self.assertTrue(self.call(HTTP_TRANSFER_ENCODING='chunked')[0].startswith('400'))
        self.router.add('GET', '/bad', lambda r, p: Response(200, b'x', {'X-Test': 'bad\r\nHeader: val'}), public=True)
        self.assertTrue(self.call(PATH_INFO='/bad')[0].startswith('500'))

    def test_decoded_path_delimiters_cannot_change_routes(self):
        for path in ('/api/health?ignored', '/api/health#ignored', '//host/api/health', '/api/health\x00'):
            with self.subTest(path=path):
                self.assertTrue(self.call(PATH_INFO=path)[0].startswith('400'))
        self.assertTrue(self.call(QUERY_STRING='x=1#fragment')[0].startswith('400'))
        self.assertTrue(self.call(QUERY_STRING='x=%3F%23')[0].startswith('200'))

    def test_invalid_special_and_duplicate_headers(self):
        for value in ('bad\r\nInjected: yes', 'bad\x00', None, 3):
            with self.subTest(value=value):
                self.assertTrue(self.call(CONTENT_TYPE=value)[0].startswith('400'))
        self.assertTrue(self.call(HTTP_AUTHORIZATION='Bearer test', HTTP_authorization='other')[0].startswith('400'))
        self.assertTrue(self.call(HTTP_CONTENT_TYPE='application/json', CONTENT_TYPE='text/plain')[0].startswith('400'))
        self.assertTrue(self.call(**{'HTTP_BAD:NAME': 'value'})[0].startswith('400'))
        self.assertTrue(self.call(HTTP_CONTENT_LENGTH='0', CONTENT_LENGTH='0')[0].startswith('400'))

    def test_invalid_lengths_and_method_types(self):
        for value in (None, False, 0):
            with self.subTest(value=value):
                self.assertTrue(self.call(CONTENT_LENGTH=value)[0].startswith('400'))
        self.assertTrue(self.call(REQUEST_METHOD='GET\tbad')[0].startswith('400'))

    def test_malformed_response_is_redacted_500(self):
        for index, response in enumerate((Response(200, 'not bytes'), Response(True, b'x'),
                                         Response(999, b'x'), Response(200, b'x', {'bad:name': 'x'}),
                                         Response(200, b'x', {'X': None}), Response(200, b'x', {'X': '\x00'}),
                                         Response(200, b'x', {'X': '\u2603'}), None)):
            path = '/malformed' + str(index)
            self.router.add('GET', path, lambda r, p, response=response: response, public=True)
            status, _, body = self.call(PATH_INFO=path)
            self.assertTrue(status.startswith('500'))
            self.assertEqual(body, b'{"error":"Internal server error"}\n')

    def test_hop_by_hop_headers_are_removed(self):
        self.router.add('GET', '/framing', lambda r, p: Response(200, b'x', {
            'Connection': 'upgrade, X-Hop', 'X-Hop': 'strip me', 'Keep-Alive': 'timeout=5', 'TE': 'trailers',
            'Trailer': 'X-Foo', 'Upgrade': 'websocket', 'Transfer-Encoding': 'chunked',
            'Content-Length': '999', 'X-Content-Type-Options': 'unsafe'}), public=True)
        status, headers, body = self.call(PATH_INFO='/framing')
        self.assertEqual(status, '200 OK')
        self.assertEqual(headers, {'Content-Length': '1', 'X-Content-Type-Options': 'nosniff'})
        self.assertEqual(body, b'x')

    def test_head_and_no_content_statuses_never_emit_body(self):
        self.router.add('HEAD', '/head', lambda r, p: Response(200, b'hello'), public=True)
        status, headers, body = self.call(REQUEST_METHOD='HEAD', PATH_INFO='/head')
        self.assertEqual(status, '200 OK')
        self.assertEqual(headers['Content-Length'], '5')
        self.assertEqual(body, b'')
        for code in (204, 304):
            path = '/status' + str(code)
            self.router.add('GET', path, lambda r, p, code=code: Response(code, b'should not be emitted'), public=True)
            _, headers, body = self.call(PATH_INFO=path)
            self.assertNotIn('Content-Length', headers)
            self.assertEqual(body, b'')


if __name__ == '__main__':
    unittest.main()
