"""Top-level Skills creation accounting; synthetic dispatch, no socket/DB/provider.

These tests exercise original host ownership around controlled response outcomes,
not filesystem creation correctness or a rendered frontend.
"""
import io
import unittest
from unittest.mock import Mock

from artex.runtime_lease import RuntimeLease, LeaseUnavailable
import test_skills_workspace_host as fixtures


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.Tests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.instance = self.f.prepare()

    def assert_held(self):
        instance = self.instance
        self.assertTrue(instance._unknown)
        self.assertTrue(instance._sealed)
        self.assertFalse(instance._active)
        self.assertEqual(instance.stop(0).reason, 'mutation_unknown')
        self.assertEqual(self.f.request(instance)[0], 503)
        with self.assertRaises(LeaseUnavailable): RuntimeLease(instance._lease.path).acquire()

    def test_top_level_post_unknown_response_seals_and_retains_original_lease(self):
        def unknown(env, start):
            start('500 Internal Server Error', [])
            return [b'{"outcome":"unknown"}']
        self.instance._application = unknown
        self.assertEqual(self.f.request(self.instance, '/api/skills', 'POST', {'name':'new'})[0], 500)
        self.assert_held()

    def test_normalized_top_level_post_exception_is_accounted_as_mutation(self):
        def failing(env, start): raise RuntimeError('synthetic-private-detail')
        self.instance._application = failing
        status, body, _ = self.f.request(self.instance, '/api/skills', 'post', {'name':'new'})
        self.assertEqual(status, 503)
        self.assertNotIn(b'synthetic-private-detail', body)
        self.assert_held()

    def test_dispatched_success_abandoned_before_iteration_completion_is_unknown(self):
        def success(env, start):
            start('201 Created', [])
            return [b'{"name":"new"}\n']
        self.instance._application = success
        env = {'REQUEST_METHOD':'POST','PATH_INFO':'/api/skills','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        result = self.instance.application(env, lambda *args: None)
        self.assertEqual(next(result), b'{"name":"new"}\n')
        self.assertTrue(self.instance._active)
        result.close()
        self.assert_held()

    def test_exceptional_mutation_delegate_close_is_unknown(self):
        class Body:
            def __iter__(self): return iter([b'{"name":"new"}\n'])
            def close(self): raise OSError('synthetic-private-close')
        def success(env, start): start('201 Created', []); return Body()
        self.instance._application = success
        self.assertEqual(self.f.request(self.instance, '/api/skills', 'POST', {'name':'new'})[0], 201)
        self.assert_held()

    def test_safe_refusals_and_catalog_read_failure_do_not_quarantine(self):
        for code in (400, 401, 403, 409):
            def refused(env, start): start(f'{code} Refused', []); return [b'{}']
            self.instance._application = refused
            self.assertEqual(self.f.request(self.instance, '/api/skills', 'POST', {'name':'new'})[0], code)
            self.assertFalse(self.instance._unknown)
            self.assertFalse(self.instance._sealed)
        def unavailable(env, start): start('503 Service Unavailable', []); return [b'{}']
        self.instance._application = unavailable
        self.assertEqual(self.f.request(self.instance, '/api/skills', 'GET')[0], 503)
        self.assertFalse(self.instance._unknown)
        self.assertTrue(self.instance.stop().stopped)

    def test_unstarted_create_response_close_does_not_dispatch_or_quarantine(self):
        application = Mock(); self.instance._application = application
        env = {'REQUEST_METHOD':'POST','PATH_INFO':'/api/skills','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        result = self.instance.application(env, lambda *args: None)
        result.close()
        application.assert_not_called()
        self.assertFalse(self.instance._active)
        self.assertFalse(self.instance._unknown)
        self.assertTrue(self.instance.stop().stopped)


if __name__ == '__main__': unittest.main()
