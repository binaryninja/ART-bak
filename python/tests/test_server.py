import json
import unittest
from artex.server import (Request, Router, AuthService, sign_jwt, verify_jwt,
                          extract_token, validate_password, json_response)


class Store:
    def __init__(self):
        self.value = None
        self.broken = False
        self.race = False

    def get(self, key):
        if self.broken:
            raise RuntimeError('private database information')
        return self.value

    def insert_if_absent(self, key, value):
        if self.value is not None or self.race:
            return False
        self.value = value
        return True

    def set(self, key, value):
        self.value = value


class TestOnlyCodec:
    """Not for application use; tests the dependency contract, not bcrypt."""
    def hash(self, password):
        return 'TEST-ONLY:' + password

    def verify(self, password, encoded):
        return encoded == self.hash(password)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.key = b'k' * 32
        self.store = Store()
        self.auth = AuthService(self.store, TestOnlyCodec(), self.key, clock=lambda: 1000)
        self.router = self.auth.router('test')

    def request(self, action, data=None, token=None, method='POST'):
        return self.router.dispatch(Request(method, '/api/auth/' + action,
            {'Authorization': 'Bearer ' + token} if token else {}, json.dumps(data or {}).encode()))

    def test_health_contract(self):
        response = self.router.dispatch(Request('GET', '/api/health'))
        self.assertEqual(response.json(), {'ok': True, 'service': 'artex', 'version': 'test'})
        self.assertEqual(response.headers['Content-Type'], 'application/json')

    def test_initial_status_and_init(self):
        self.assertEqual(self.request('status', method='GET').json(), {'initialized': False})
        result = self.request('init', {'password': 'abcdefgh'})
        self.assertEqual(result.status, 200)
        self.assertTrue(verify_jwt(result.json()['token'], self.key, now=1001))
        self.assertEqual(self.request('init', {'password': 'ijklmnop'}).status, 403)

    def test_init_race_is_denied(self):
        self.store.race = True
        self.assertEqual(self.request('init', {'password': 'abcdefgh'}).status, 403)

    def test_database_read_failure_never_initializes(self):
        self.store.broken = True
        for name in ('status', 'init', 'login'):
            result = self.request(name, {'password': 'abcdefgh'}, method='GET' if name == 'status' else 'POST')
            self.assertEqual(result.status, 503)
            self.assertNotIn(b'private', result.body)
        self.assertIsNone(self.store.value)

    def test_login_credentials(self):
        self.request('init', {'password': 'abcdefgh'})
        self.assertEqual(self.request('login', {'username': 'ARTEX', 'password': 'abcdefgh'}).status, 200)
        self.assertEqual(self.request('login', {'username': 'other', 'password': 'abcdefgh'}).status, 401)
        self.assertEqual(self.request('login', {'username': 'ARTEX', 'password': 'wrong'}).status, 401)

    def test_change_requires_token_and_old_password(self):
        token = self.request('init', {'password': 'abcdefgh'}).json()['token']
        data = {'old_password': 'abcdefgh', 'new_password': 'ijklmnop'}
        self.assertEqual(self.request('change-password', data).status, 401)
        self.assertEqual(self.request('change-password', {**data, 'old_password': 'bad'}, token).status, 401)
        self.assertEqual(self.request('change-password', data, token).json(), {'ok': True})
        self.assertEqual(self.request('login', {'username': 'ARTEX', 'password': 'ijklmnop'}).status, 200)

    def test_password_bounds(self):
        self.assertTrue(validate_password('short'))
        self.assertFalse(validate_password('a' * 72))
        self.assertTrue(validate_password('a' * 73))
        self.assertFalse(validate_password('\U0001f642' * 8))
        self.assertTrue(validate_password('\U0001f642' * 19))

    def test_jwt_expiry_and_tampering(self):
        token = sign_jwt(self.key, now=1000)
        self.assertTrue(verify_jwt(token, self.key, now=1000))
        self.assertFalse(verify_jwt(token, self.key, now=1000 + 604800))
        self.assertFalse(verify_jwt(token, b'x' * 32, now=1000))
        self.assertFalse(verify_jwt(token + 'x', self.key, now=1000))
        for value in ('', 'a.b.c', 'null.e30.x', 'a' * 20000):
            self.assertFalse(verify_jwt(value, self.key))

    def test_token_source_priority(self):
        request = Request('GET', '/?token=query', {'authorization': 'Bearer header', 'Cookie': 'artex_token=cookie'})
        self.assertEqual(extract_token(request), 'header')
        self.assertEqual(extract_token(Request('GET', '/?token=query', {'Cookie': 'artex_token=cookie'})), 'cookie')
        self.assertEqual(extract_token(Request('GET', '/?token=query')), 'query')

    def test_bad_json_and_types(self):
        for body in (b'[]', b'{', b'{"password":42}', b'{"password":NaN}'):
            self.assertEqual(self.router.dispatch(Request('POST', '/api/auth/init', body=body)).status, 400)

    def test_unknown_auth_route_not_exempt(self):
        self.assertEqual(self.router.dispatch(Request('GET', '/api/auth/secret')).status, 401)

    def test_router_parameters_precedence_and_errors(self):
        router = Router(lambda r: True)
        router.add('GET', '/items/{id}', lambda r, p: json_response(200, p))
        router.add('GET', '/items/new', lambda r, p: json_response(201, {}))
        self.assertEqual(router.dispatch(Request('GET', '/items/new')).status, 201)
        self.assertEqual(router.dispatch(Request('GET', '/items/123?q=x')).json(), {'id': '123'})
        self.assertEqual(router.dispatch(Request('POST', '/items/123')).status, 405)
        self.assertEqual(router.dispatch(Request('GET', '/missing')).status, 404)
        with self.assertRaises(ValueError):
            router.add('GET', '/items/new', lambda r, p: None)

    def test_auth_errors_fail_closed(self):
        def broken(request):
            raise RuntimeError('secret')
        router = Router(broken)
        router.add('GET', '/private', lambda r, p: json_response(200, {}))
        self.assertEqual(router.dispatch(Request('GET', '/private')).status, 401)

    def test_handler_errors_redacted(self):
        router = Router(lambda r: True)
        def broken(r, p):
            raise RuntimeError('secret')
        router.add('GET', '/broken', broken)
        response = router.dispatch(Request('GET', '/broken'))
        self.assertEqual(response.status, 500)
        self.assertNotIn(b'secret', response.body)


if __name__ == '__main__':
    unittest.main()
