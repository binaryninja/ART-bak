"""Ordinary metadata error classification, not tool execution or live SQL.

Original store over the existing queued-SQL fixture records transaction events.
Those events are test observations, not PostgreSQL commit/durability evidence.
"""
import io
import json
import unittest
from unittest.mock import Mock, patch

from artex.db.tool_catalog import CatalogTool, ToolCatalogStore
from artex.server.auth import AuthService, sign_jwt
from artex.server.tool_catalog import ToolCatalogService
from artex.server.wsgi import WSGIApplication
from test_tool_catalog import ROW, SQL


FAULTS = (ValueError, TypeError, UnicodeError, RecursionError)
INPUT = dict(key='fixture', description='Synthetic metadata', schema={},
             agents=['custom'], enabled=False, kind='command', exec={}, deferred=False)


class ToolMetadataErrorTests(unittest.TestCase):
    def setUp(self):
        self.db = SQL()
        self.store = ToolCatalogStore(self.db)
        self.service = ToolCatalogService(self.store)
        key = b'synthetic-tool-metadata-key-32-bytes'
        auth = AuthService(None, None, key, clock=lambda: 1000)
        self.token = sign_jwt(key, now=1000)
        router = auth.router()
        self.service.register(router)
        # Let the service's existing one-MiB limit be observed separately.
        self.application = WSGIApplication(router, max_body_bytes=2 << 20)

    def request(self, method='POST', path='/api/tools/custom', body=None, *, auth=True):
        if body is None:
            body = dict(INPUT)
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        env = dict(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING='',
                   CONTENT_LENGTH=str(len(body)), CONTENT_TYPE='application/json')
        env['wsgi.input'] = io.BytesIO(body)
        if auth:
            env['HTTP_AUTHORIZATION'] = 'Bearer ' + self.token
        captured = []
        result = self.application(env, lambda status, headers: captured.append((status, dict(headers))))
        payload = b''.join(result)
        status, headers = captured[0]
        self.assertEqual(int(headers['Content-Length']), len(payload))
        return int(status.split()[0]), json.loads(payload)

    def test_unauthenticated_input_never_reaches_store(self):
        self.assertEqual(self.request(body=b'not JSON', auth=False)[0], 401)
        self.assertEqual((self.db.calls, self.db.events), ([], []))

    def test_malformed_input_remains_400_before_any_store_call(self):
        payloads = [b'', b'[]', b'null', b'{', b'{"key":"fixture","key":"other"}',
                    b'{"key":"fixture","schema":{"x":NaN}}', b'\xff',
                    b'\xef\xbb\xbf{}', '{}'.encode('utf-16'), '{}'.encode('utf-32'),
                    b'{"schema":' + b'[' * 12000 + b'0' + b']' * 12000 + b'}',
                    {**INPUT, 'approval': True}, {**INPUT, 'key': '../other'},
                    {**INPUT, 'enabled': 1}, {**INPUT, 'agents': [{}]},
                    {**INPUT, 'schema': []}, {**INPUT, 'exec': []}]
        for body in payloads:
            with self.subTest(body_type=type(body).__name__):
                self.assertEqual(self.request(body=body), (400, {'error': 'Invalid tool metadata'}))
                self.assertEqual((self.db.calls, self.db.events), ([], []))

    def test_existing_oversize_refusal_remains_413(self):
        self.assertEqual(self.request(body=b' ' * (1048576 + 1))[0], 413)
        self.assertEqual((self.db.calls, self.db.events), ([], []))

    def test_escaped_surrogate_description_stays_400_before_mutation(self):
        for description in ('\ud800', '\udfff', 'text\ud800end', '\udfff\ud800'):
            for method, path, results in (
                ('POST', '/api/tools/custom', []),
                ('PUT', '/api/tools/custom/fixture', []),
                ('PUT', '/api/tools/fixture', [[ROW]]),
            ):
                with self.subTest(description=ascii(description), path=path):
                    payload = {**INPUT, 'description': description}
                    if path == '/api/tools/fixture':
                        payload = {key: payload[key] for key in ('description', 'schema', 'agents', 'enabled')}
                    self.db.results = list(results)
                    self.db.calls.clear(); self.db.events.clear()
                    body = json.dumps(payload, ensure_ascii=True).encode('ascii')
                    self.assertEqual(self.request(method, path, body),
                                     (400, {'error': 'Invalid tool metadata'}))
                    self.assertEqual(self.db.events, [])
                    self.assertEqual(len(self.db.calls), len(results))
                    self.assertTrue(all(q.sql.startswith('SELECT ') for q in self.db.calls))

    def test_description_valid_utf8_and_existing_byte_boundary_are_unchanged(self):
        for description in ('Synthetic 世界 \U0001f4da', '\U0001f4da' * 16384):
            with self.subTest(bytes=len(description.encode('utf-8'))):
                self.db.results = [[(1,)], ('fixture',)]
                self.db.calls.clear(); self.db.events.clear()
                body = json.dumps({**INPUT, 'description': description}, ensure_ascii=True).encode('ascii')
                self.assertEqual(self.request(body=body), (200, {'key': 'fixture'}))
                self.assertEqual(self.db.calls[-1].params[1], description)
                self.assertEqual(self.db.events, ['begin', 'commit'])
        self.db.calls.clear(); self.db.events.clear()
        self.assertEqual(self.request(body={**INPUT, 'description': 'x' * 65537})[0], 400)
        self.assertEqual((self.db.calls, self.db.events), ([], []))

    def test_decoder_faults_are_local_to_input_but_memory_error_is_internal(self):
        for error in FAULTS:
            with self.subTest(error=error.__name__), patch('artex.server.tool_catalog.json.loads', side_effect=error('private decoder detail')):
                # json.loads is shared globally, so inspect after restoring it.
                response = self.service.handle('create', self._raw_request(), {})
            self.assertEqual(response.status, 400)
            self.assertNotIn(b'private decoder', response.body)
        with patch('artex.server.tool_catalog.json.loads', side_effect=MemoryError('private memory detail')):
            response = self.service.handle('create', self._raw_request(), {})
        self.assertEqual(response.status, 500)
        self.assertNotIn(b'private memory', response.body)
        self.assertEqual((self.db.calls, self.db.events), ([], []))

    @staticmethod
    def _raw_request():
        from artex.server.core import Request
        return Request('POST', '/api/tools/custom', {}, b'{}')

    def test_internal_fault_before_store_effect_is_redacted_500(self):
        for error in FAULTS:
            with self.subTest(error=error.__name__):
                self.store.create = Mock(side_effect=error('private pre-effect detail'))
                self.assertEqual(self.request(), (500, {'error': 'Tool catalog operation failed'}))
                self.store.create.assert_called_once()
                self.assertEqual((self.db.calls, self.db.events), ([], []))

    def test_original_store_fault_inside_transaction_is_500_without_retry(self):
        for error in FAULTS:
            with self.subTest(error=error.__name__):
                self.db.results = [[(1,)], error('private query detail')]
                self.db.calls.clear(); self.db.events.clear()
                self.assertEqual(self.request(), (500, {'error': 'Tool catalog operation failed'}))
                self.assertEqual(self.db.events, ['begin', 'rollback'])
                self.assertEqual(len(self.db.calls), 2)
                self.assertTrue(self.db.calls[-1].sql.startswith('INSERT INTO tools'))

    def test_generic_fault_after_original_mutation_is_500_and_not_retried(self):
        cases = (
            ('POST', '/api/tools/custom', 'create', [[(1,)], ('fixture',)], dict(INPUT), 2),
            ('PUT', '/api/tools/custom/fixture', 'update', [[ROW], [(1,)], 1], dict(INPUT), 3),
            ('PUT', '/api/tools/fixture', 'update', [[ROW], [ROW], [(1,)], 1],
             {key: INPUT[key] for key in ('description', 'schema', 'agents', 'enabled')}, 4),
            ('DELETE', '/api/tools/custom/fixture', 'delete', [[ROW], 1], b'', 2),
        )
        for method, path, operation, results, body, count in cases:
            for error in FAULTS:
                with self.subTest(operation=operation, path=path, error=error.__name__):
                    db = SQL(results); store = ToolCatalogStore(db)
                    original = getattr(store, operation)
                    invoked = []
                    def fail_after(*args, original=original, error=error, **kwargs):
                        invoked.append(True)
                        original(*args, **kwargs)
                        raise error('private post-return detail')
                    setattr(store, operation, fail_after)
                    self.service.store = store
                    self.assertEqual(self.request(method, path, body),
                                     (500, {'error': 'Tool catalog operation failed'}))
                    self.assertEqual(invoked, [True])
                    self.assertEqual(db.events, ['begin', 'commit'])
                    self.assertEqual(len(db.calls), count)
                    self.assertEqual(sum(q.sql.startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in db.calls), 1)

    def test_response_construction_failure_after_write_remains_500(self):
        for error in FAULTS:
            with self.subTest(error=error.__name__):
                self.db.results = [[(1,)], ('fixture',)]
                self.db.calls.clear(); self.db.events.clear()
                with patch('artex.server.tool_catalog.json_response', side_effect=error('private receipt detail')):
                    self.assertEqual(self.request(), (500, {'error': 'Tool catalog operation failed'}))
                self.assertEqual(self.db.events, ['begin', 'commit'])
                self.assertEqual(len(self.db.calls), 2)

    def test_internal_read_and_dto_failures_are_not_invalid_client_input(self):
        for error in FAULTS:
            with self.subTest(error=error.__name__):
                self.db.results = [error('private read detail')]
                self.assertEqual(self.request('GET', '/api/tools', b''),
                                 (500, {'error': 'Tool catalog operation failed'}))
                self.db.results = [[ROW], []]
                with patch.object(CatalogTool, 'to_dict', side_effect=error('private DTO detail')):
                    self.assertEqual(self.request('GET', '/api/tools', b''),
                                     (500, {'error': 'Tool catalog operation failed'}))
        self.assertEqual(self.db.events, [])

    def test_explicit_store_validation_conflict_missing_and_unavailable_are_unchanged(self):
        from artex.db.postgres import NoRowsError
        # Original store's typed validation refuses an unknown binding before INSERT.
        self.db.results = [[]]
        self.assertEqual(self.request()[0], 400)
        self.assertEqual(self.db.events, ['begin', 'rollback'])
        self.assertEqual(len(self.db.calls), 1)
        self.db.results = [[(1,)], NoRowsError()]
        self.assertEqual(self.request()[0], 409)
        self.db.results = [[]]
        self.assertEqual(self.request('PUT', '/api/tools/missing', {})[0], 404)
        self.service.store = None
        self.assertEqual(self.request()[0], 503)

    def test_success_receipts_and_single_original_transaction_are_unchanged(self):
        self.db.results = [[(1,)], ('fixture',)]
        self.assertEqual(self.request(), (200, {'key': 'fixture'}))
        self.assertEqual(self.db.events, ['begin', 'commit'])
        self.db.results = [[ROW], [(1,)], 1]
        self.assertEqual(self.request('PUT', '/api/tools/custom/fixture', dict(INPUT)), (200, {'ok': True}))
        self.db.results = [[ROW], 1]
        self.assertEqual(self.request('DELETE', '/api/tools/custom/fixture', b''), (200, {'deleted': 'fixture'}))
        self.assertEqual(self.db.events, ['begin', 'commit'] * 3)
