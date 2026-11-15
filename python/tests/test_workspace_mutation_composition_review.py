"""Independent application wiring checks; no filesystem access."""
import io
import unittest
from artex.server.application import build_application, build_persisted_application
from artex.server.workspace_mutations import WorkspaceMutationService
from test_application import TestCodec
from test_tasks_store import Fake


class Review(unittest.TestCase):
    def test_auth_precedes_workspace_resolution(self):
        calls = []
        def forbidden(*args):
            calls.append(args)
            raise AssertionError('Workspace callback must not run')
        service = WorkspaceMutationService(resolve=forbidden, authorize=forbidden,
            current=forbidden, writer_guard=forbidden,
            mutation_authorize=forbidden, delete_authorize=forbidden)
        db = Fake([])
        app = build_application(db, b'x' * 32, TestCodec(), workspace_mutation_service=service)
        for method, path, query, body in [
            ('POST', '/api/workspace/mkdir', '', b'{"path":"fixture"}'),
            ('DELETE', '/api/workspace/delete', 'path=fixture', b'')]:
            status = []
            env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
                   'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}
            list(app(env, lambda s, h: status.append(s)))
            self.assertTrue(status[0].startswith('401'))
        self.assertEqual(calls, [])
        self.assertEqual(db.calls, [])

    def test_exact_service_type_before_persisted_reads(self):
        db = Fake([])
        class Other:
            def register(self, router):
                raise AssertionError('Untrusted registration')
        with self.assertRaises(ValueError):
            build_persisted_application(db, b'x' * 32, TestCodec(), workspace_mutation_service=Other())
        self.assertEqual(db.calls, [])
