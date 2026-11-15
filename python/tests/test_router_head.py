import unittest
from artex.server.core import Router, Request, json_response


class HeadTests(unittest.TestCase):
    def test_get_fallback_and_explicit_precedence(self):
        r = Router(lambda request: False)
        r.add('GET', '/health', lambda req, params: json_response(200, {'ok': True}), public=True)
        self.assertEqual(r.dispatch(Request('HEAD', '/health')).status, 200)
        r.add('HEAD', '/health', lambda req, params: json_response(204, {}), public=True)
        self.assertEqual(r.dispatch(Request('HEAD', '/health')).status, 204)

    def test_protected_head_and_allow(self):
        r = Router(lambda request: request.header('Authorization') == 'yes')
        r.add('GET', '/private', lambda req, params: json_response(200, {}))
        self.assertEqual(r.dispatch(Request('HEAD', '/private')).status, 401)
        response = r.dispatch(Request('PUT', '/private', {'Authorization': 'yes'}))
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers['Allow'], 'GET, HEAD')
