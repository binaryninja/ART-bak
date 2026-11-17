"""Configured Skills-only mode is a bounded hint, not mutation authority."""
import io
import json
import os
import select
import signal
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

from artex import skills_workspace as host
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.auth import sign_jwt, JWT_TTL
from artex.server.core import Request
import test_skills_workspace_host as fixtures
import test_skills_workspace_transport as transport

PATH = '/api/skills-workspace'


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.Tests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_exact_read_only_and_write_enabled_shapes_no_paths_or_secrets(self):
        for mode in (False, True):
            config = host.WorkspaceConfig(str(self.f.root), str(self.f.password), True, mode)
            instance = self.f.prepare(config)
            status, body, headers = self.f.request(instance, PATH)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {'profile':'skills-workspace', 'writes_enabled':mode})
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertLess(len(body), 100)
            for secret in (str(self.f.root), str(self.f.password), 'synthetic-test-password'):
                self.assertNotIn(secret.encode(), body)
            head_status, empty, head_headers = self.f.request(instance, PATH, 'HEAD')
            self.assertEqual((head_status, empty, head_headers['Content-Length']), (200, b'', str(len(body))))
            self.assertEqual(head_headers['Cache-Control'], 'no-store')
            self.assertTrue(instance.stop().stopped)

    def test_authentication_and_method_boundaries_without_mutation(self):
        instance = self.f.prepare()
        for method in ('GET', 'HEAD', 'POST', 'PUT', 'DELETE', 'OPTIONS'):
            self.assertEqual(self.f.request(instance, PATH, method, token=False)[0], 401)
        for method in ('POST', 'PUT', 'DELETE', 'OPTIONS'):
            status, _, headers = self.f.request(instance, PATH, method, {'writes_enabled':True})
            self.assertEqual(status, 405)
            self.assertEqual(headers['Allow'], 'GET, HEAD')
        self.assertFalse(instance._unknown)
        self.assertTrue(instance.stop().stopped)

    def test_body_and_query_refused_not_accepted_as_configuration(self):
        instance = self.f.prepare()
        self.assertEqual(self.f.request(instance, PATH, body={'writes_enabled':True})[0], 400)
        env = {'REQUEST_METHOD':'GET', 'PATH_INFO':PATH, 'QUERY_STRING':'writes_enabled=true',
               'CONTENT_LENGTH':'0', 'wsgi.input':io.BytesIO(),
               'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
        result = []
        b''.join(instance.application(env, lambda status, headers: result.append(status)))
        self.assertEqual(result, ['400 Bad Request'])
        self.assertFalse(instance._unknown)
        self.assertTrue(instance.stop().stopped)

    def test_not_mounted_in_an_ordinary_auth_router_or_callable_without_host_admission(self):
        instance = self.f.prepare()
        request = Request('GET', PATH, {'Authorization':'Bearer '+sign_jwt(instance._auth.key)})
        self.assertEqual(instance._auth.router().dispatch(request).status, 404)
        self.assertEqual(instance._profile(request, {}).status, 503)
        self.assertTrue(instance.stop().stopped)

    def test_read_only_hint_does_not_enable_actual_create_or_file_writes(self):
        instance = self.f.prepare(host.WorkspaceConfig(str(self.f.root), str(self.f.password), True))
        self.assertFalse(json.loads(self.f.request(instance, PATH)[1])['writes_enabled'])
        self.assertEqual(self.f.request(instance, '/api/skills', 'POST', {'name':'denied','description':'Synthetic'})[0], 403)
        self.assertEqual(self.f.request(instance, '/api/skills/sample/files/denied', 'PUT', {'content':'x'})[0], 403)
        self.assertFalse((self.f.root/'denied').exists())
        self.assertFalse((self.f.skill/'denied').exists())
        self.assertFalse(instance._unknown)
        self.assertTrue(instance.stop().stopped)

    def test_new_request_after_seal_or_quarantine_never_returns_cached_mode(self):
        instance = self.f.prepare()
        self.assertTrue(json.loads(self.f.request(instance, PATH)[1])['writes_enabled'])
        instance._quarantine()
        self.assertEqual(self.f.request(instance, PATH)[0], 503)
        self.assertEqual(self.f.request(instance, PATH, 'HEAD')[:2], (503,b''))
        self.assertEqual(instance.stop().reason, 'mutation_unknown')
        with self.assertRaises(LeaseUnavailable): RuntimeLease(instance._lease.path).acquire()

    def test_post_header_jwt_expiry_or_config_replacement_withholds_body(self):
        for change in ('jwt', 'config'):
            instance = self.f.prepare()
            config, clock = instance._config, instance._auth.clock
            env = {'REQUEST_METHOD':'GET', 'PATH_INFO':PATH, 'CONTENT_LENGTH':'0',
                   'wsgi.input':io.BytesIO(), 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
            statuses = []
            def start(status, headers):
                statuses.append(status)
                if change == 'jwt': instance._auth.clock = lambda: clock()+JWT_TTL*2
                else: instance._config = host.WorkspaceConfig(str(self.f.root), str(self.f.password), True)
            try:
                self.assertEqual(b''.join(instance.application(env, start)), b'')
                self.assertEqual(statuses, ['200 OK'])
                self.assertFalse(instance._unknown)
            finally:
                instance._config, instance._auth.clock = config, clock
            self.assertTrue(instance.stop().stopped)

    def test_root_identity_failure_redacts_profile_without_quarantine_bypass(self):
        instance = self.f.prepare()
        self.f.root.chmod(0o755)
        try:
            status, body, _ = self.f.request(instance, PATH)
            self.assertEqual(status, 503)
            self.assertNotIn(str(self.f.root).encode(), body)
            self.assertNotIn(b'writes_enabled', body)
        finally: self.f.root.chmod(0o700)
        self.assertTrue(instance.stop().stopped)


class ProfileTransportTests(unittest.TestCase):
    def setUp(self):
        self.t = transport.Tests(); self.t.setUp(); self.addCleanup(self.t.doCleanups)

    def test_actual_http_read_only_profile_auth_head_and_host_origin_boundaries(self):
        self.t.config = host.WorkspaceConfig(str(self.t.f.root), str(self.t.f.password), True, False, self.t.port)
        instance = self.t.start()
        self.assertEqual(self.t.send(PATH)[0], 401)
        token = self.t.login()
        status, body, headers = self.t.send(PATH, token=token)
        self.assertEqual((status, json.loads(body)), (200, {'profile':'skills-workspace','writes_enabled':False}))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        hs, empty, hh = self.t.send(PATH, 'HEAD', token=token)
        self.assertEqual((hs, empty, hh['Content-Length']), (200,b'',str(len(body))))
        self.assertEqual(self.t.send(PATH+'?writes_enabled=true', token=token)[0], 400)
        for fields in ({'Host':'evil.invalid'}, {'Origin':'null'}, {'Origin':'https://evil.invalid'},
                       {'Host':'evil.invalid','X-Forwarded-Host':f'127.0.0.1:{self.t.port}'}):
            self.assertEqual(self.t.send(PATH, token=token, headers=fields)[0], 403)
        self.assertTrue(instance.stop(3).stopped)

    def test_actual_fsync_unknown_makes_profile_unavailable_and_keeps_lease(self):
        instance = self.t.start(); token = self.t.login()
        self.assertTrue(json.loads(self.t.send(PATH, token=token)[1])['writes_enabled'])
        original = os.fsync
        def fault(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError('synthetic private durability failure')
            return original(fd)
        with patch('artex.tools.filesystem.os.fsync', side_effect=fault):
            self.assertEqual(self.t.send('/api/skills/sample/files/new', 'PUT', {'content':'inert'}, token)[0], 500)
        self.assertEqual(self.t.send(PATH, token=token)[0], 503)
        self.assertEqual(instance.stop(3).reason, 'mutation_unknown')
        with self.assertRaises(LeaseUnavailable): RuntimeLease(instance._lease.path).acquire()

    def test_actual_default_cli_profile_false_then_natural_shutdown(self):
        env = {'PATH':os.environ.get('PATH','/usr/bin:/bin'), 'PYTHONPATH':os.pathsep.join(sys.path),
               'HOME':str(self.t.f.path), 'TMPDIR':str(self.t.f.path), 'PYTHONNOUSERSITE':'1',
               'PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'}
        command = [sys.executable,'-m','artex.skills_workspace','--root',str(self.t.f.root),
                   '--password-file',str(self.t.f.password),'--exclusive-owner','--port',str(self.t.port)]
        child = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
        try:
            ready,_,_ = select.select([child.stdout],[],[],8)
            self.assertTrue(ready)
            line = child.stdout.readline(); self.assertIn(b'Skills workspace ready',line)
            token = self.t.login()
            self.assertEqual(json.loads(self.t.send(PATH,token=token)[1])['writes_enabled'],False)
            self.assertEqual(self.t.send('/api/skills','POST',{'name':'denied','description':'Synthetic'},token)[0],403)
            self.assertFalse((self.t.f.root/'denied').exists())
            os.kill(child.pid,signal.SIGTERM)
            stdout,stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode,0,stderr.decode())
            self.assertNotIn(b'synthetic-test-password',line+stdout+stderr)
            self.assertNotIn(token.encode(),line+stdout+stderr)
            lease=RuntimeLease(str(self.t.f.root/'.artex-skills.lock')); self.assertTrue(lease.acquire()); lease.release()
        finally:
            # Failure containment for this synthetic child only, never passing drain.
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGKILL); child.communicate(timeout=5)


if __name__ == '__main__': unittest.main()
