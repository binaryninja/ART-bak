"""Fake-driver adapter contracts only; not cryptographic implementation tests."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from artex.server import AuthService, Request
from artex.server.passwords import BcryptCodec, BcryptUnavailable, PasswordHashError

HASH = '$2b$10$' + '.' * 53


class FakeDriver:
    """Never used outside tests; deliberately does not implement cryptography."""
    def __init__(self):
        self.calls = []
        self.result = True
        self.hash_value = HASH.encode()
        self.error = None
    def gensalt(self, rounds=12, prefix=b'2b'):
        self.calls.append(('salt', rounds, prefix))
        return b'TEST-ONLY-SALT'
    def hashpw(self, password, salt):
        self.calls.append(('hash', password, salt))
        if self.error:
            raise self.error
        return self.hash_value
    def checkpw(self, password, hashed_password):
        self.calls.append(('verify', password, hashed_password))
        if self.error:
            raise self.error
        return self.result


class PasswordCodecTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.codec = BcryptCodec(driver=self.driver)

    def test_hash_encodes_utf8_and_requests_random_backend_salt(self):
        password = 'dummypassword'
        self.assertEqual(self.codec.hash(password), HASH)
        self.assertEqual(self.driver.calls, [('salt', 10, b'2b'), ('hash', password.encode(), b'TEST-ONLY-SALT')])
        self.codec.hash(password)
        self.assertEqual(sum(c[0] == 'salt' for c in self.driver.calls), 2)

    def test_verify_uses_checkpw_not_hash_equality(self):
        self.assertTrue(self.codec.verify('dummypassword', HASH))
        self.assertEqual(self.driver.calls, [('verify', b'dummypassword', HASH.encode())])
        self.driver.result = False
        self.assertFalse(self.codec.verify('dummypassword', HASH))
        self.driver.result = 'truthy but not boolean'
        self.assertFalse(self.codec.verify('dummypassword', HASH))

    def test_minimum_is_eight_characters_not_bytes(self):
        # Seven two-byte characters exceed eight bytes but still fail policy.
        for value in ('short', '\u00e9' * 7):
            with self.assertRaises(ValueError):
                self.codec.hash(value)
            self.assertFalse(self.codec.verify(value, HASH))
        self.assertEqual(self.driver.calls, [])
        self.codec.hash('\u00e9' * 8)
        self.assertEqual(self.driver.calls[-1][1], ('\u00e9' * 8).encode())

    def test_utf8_byte_limit_and_no_truncation(self):
        for value in ('a' * 72, '\u00e9' * 36, '\U0001f642' * 18):
            self.codec.hash(value)
            self.assertEqual(len(self.driver.calls[-1][1]), 72)
        self.driver.calls.clear()
        for value in ('a' * 73, '\u00e9' * 37, '\U0001f642' * 19):
            with self.assertRaises(ValueError):
                self.codec.hash(value)
            self.assertFalse(self.codec.verify(value, HASH))
        self.assertEqual(self.driver.calls, [])

    def test_invalid_type_and_unicode_fail_closed(self):
        for value in (None, 123, b'dummypassword', 'dummy\ud800password'):
            with self.assertRaises(ValueError):
                self.codec.hash(value)
            self.assertFalse(self.codec.verify(value, HASH))
        self.assertEqual(self.driver.calls, [])

    def test_nul_is_passed_unchanged_to_modern_backend(self):
        self.codec.hash('dummy\0password')
        self.assertEqual(self.driver.calls[-1][1], b'dummy\0password')

    def test_existing_prefixes_and_work_factors(self):
        for prefix in ('2a', '2b', '2y'):
            value = f'${prefix}$10$' + '.' * 53
            self.assertTrue(self.codec.verify('dummypassword', value))
            self.assertEqual(self.driver.calls[-1][-1], value.encode())

    def test_malformed_stored_hash_never_reaches_driver(self):
        for value in (None, '', 'plaintext', HASH[:-1], HASH+'x', HASH.replace('$10$', '$03$'),
                      HASH.replace('$10$', '$32$'), HASH.replace('$2b$', '$2x$'),
                      HASH.replace('.', '\u00e9'), HASH+'\n'):
            self.assertFalse(self.codec.verify('dummypassword', value))
        self.assertEqual(self.driver.calls, [])

    def test_rounds_validation(self):
        for rounds in (True, False, '12', 12.0, 0, 3, 32):
            with self.assertRaises(ValueError):
                BcryptCodec(rounds=rounds)
        for rounds in (4, 10, 12, 31):
            BcryptCodec(rounds=rounds, driver=self.driver)

    def test_driver_failures_do_not_expose_passwords(self):
        self.driver.error = RuntimeError('secret password and salt')
        with self.assertRaisesRegex(PasswordHashError, '^bcrypt password hashing failed$'):
            self.codec.hash('dummypassword')
        with self.assertRaisesRegex(PasswordHashError, '^bcrypt password verification failed$'):
            self.codec.verify('dummypassword', HASH)
        self.driver.error = ValueError('invalid salt')
        self.assertFalse(self.codec.verify('dummypassword', HASH))

    def test_invalid_driver_hash_result_never_stored(self):
        for value in (HASH, b'plaintext', b'\xff', b'$2a$12$'+b'.'*53, b'$2b$12$'+b'.'*53):
            self.driver.hash_value = value
            with self.assertRaises(PasswordHashError):
                self.codec.hash('dummypassword')

    def test_lazy_optional_import_and_cache(self):
        module = SimpleNamespace(__version__='5.0.0', gensalt=self.driver.gensalt,
                                 hashpw=self.driver.hashpw, checkpw=self.driver.checkpw)
        with patch('artex.server.passwords.importlib.import_module', return_value=module) as load:
            codec = BcryptCodec()
            load.assert_not_called()
            codec.hash('dummypassword')
            codec.verify('dummypassword', HASH)
            load.assert_called_once_with('bcrypt')

    def test_missing_dependency_has_no_fallback(self):
        with patch('artex.server.passwords.importlib.import_module', side_effect=ModuleNotFoundError('bcrypt')):
            codec = BcryptCodec()
            with self.assertRaises(BcryptUnavailable):
                codec.hash('dummypassword')
            with self.assertRaises(BcryptUnavailable):
                codec.verify('dummypassword', HASH)

    def test_unsupported_versions_and_invalid_backend_api_fail_closed(self):
        for version in ('4.3.0', '6.0.0', '', None, '5.0.0rc1'):
            with patch('artex.server.passwords.importlib.import_module', return_value=SimpleNamespace(__version__=version)):
                with self.assertRaises(BcryptUnavailable):
                    BcryptCodec().hash('dummypassword')
        with patch('artex.server.passwords.importlib.import_module', return_value=SimpleNamespace(__version__='5.0.0')):
            with self.assertRaises(BcryptUnavailable):
                BcryptCodec().hash('dummypassword')

    def test_invalid_inputs_do_not_import_optional_dependency(self):
        with patch('artex.server.passwords.importlib.import_module') as load:
            codec = BcryptCodec()
            with self.assertRaises(ValueError):
                codec.hash('a' * 73)
            self.assertFalse(codec.verify('dummypassword', 'plaintext'))
            load.assert_not_called()

    def test_authservice_uses_codec_and_stores_only_hash(self):
        class Store:
            value = None
            def get(self, key): return self.value
            def insert_if_absent(self, key, value):
                if self.value is not None: return False
                self.value = value
                return True
        store = Store()
        router = AuthService(store, self.codec, b'k' * 32, clock=lambda: 1000).router()
        response = router.dispatch(Request('POST', '/api/auth/init', body=json.dumps({'password': 'dummypassword'}).encode()))
        self.assertEqual(response.status, 200)
        self.assertEqual(store.value, HASH)
        self.assertNotIn(b'dummypassword', response.body)
        self.driver.result = False
        response = router.dispatch(Request('POST', '/api/auth/login', body=json.dumps({'username': 'ARTEX', 'password': 'wrongpassword'}).encode()))
        self.assertEqual(response.status, 401)

    def test_authservice_does_not_write_if_dependency_missing(self):
        class Store:
            def get(self, key): return None
            def insert_if_absent(self, key, value): raise AssertionError('Must not write credentials')
        with patch('artex.server.passwords.importlib.import_module', side_effect=ModuleNotFoundError):
            router = AuthService(Store(), BcryptCodec(), b'k' * 32).router()
            response = router.dispatch(Request('POST', '/api/auth/init', body=b'{"password":"dummypassword"}'))
        self.assertEqual(response.status, 500)
        self.assertNotIn(b'dummypassword', response.body)


if __name__ == '__main__':
    unittest.main()
