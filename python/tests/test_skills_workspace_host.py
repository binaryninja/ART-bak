"""Skills-only host contracts: temporary files, real bcrypt/JWT, no socket binds."""
import io
import json
import os
import signal
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from artex import skills_workspace as host
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.auth import sign_jwt


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='artex-skills-host-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.root = self.path / 'skills'; self.root.mkdir(mode=0o700)
        self.skill = self.root / 'sample'; self.skill.mkdir(mode=0o700)
        (self.skill / 'SKILL.md').write_text('---\nname: sample\ndescription: synthetic\n---\n# Inert\n')
        self.password = self.path / 'password'; self.password.write_text('synthetic-test-password\n'); self.password.chmod(0o600)
        self.config = host.WorkspaceConfig(str(self.root), str(self.password), True, True)
        self.instances = []
        self.addCleanup(self.release_fixture_only)

    def release_fixture_only(self):
        for instance in self.instances:
            # Test teardown, never evidence of a clean unresolved production stop.
            if instance._lease._fd is not None:
                instance._lease.release(owner=instance, permit=instance._permit)

    def prepare(self, config=None):
        value = host.SkillsWorkspaceHost.prepare(config or self.config)
        self.instances.append(value)
        return value

    def request(self, instance, path='/api/skills', method='GET', body=None, token=True):
        raw = json.dumps(body).encode() if body is not None else b''
        env = {'REQUEST_METHOD':method, 'PATH_INFO':path, 'QUERY_STRING':'', 'CONTENT_LENGTH':str(len(raw)), 'wsgi.input':io.BytesIO(raw)}
        if token: env['HTTP_AUTHORIZATION'] = 'Bearer '+sign_jwt(instance._auth.key)
        result = []
        payload = b''.join(instance.application(env, lambda status, headers: result.append((int(status.split()[0]),dict(headers)))))
        return result[0][0], payload, result[0][1]

    def test_prepare_is_listen_inert_and_already_initialized(self):
        with patch.object(host, 'make_server') as bind:
            instance = self.prepare(); bind.assert_not_called()
        self.assertFalse(instance._allowed())
        self.assertIsNone(instance._server)
        self.assertEqual(self.request(instance, '/api/auth/status', token=False)[1], b'{"initialized":true}\n')
        self.assertEqual(self.request(instance, '/api/auth/init', 'POST', {'password':'attacker-password'}, False)[0], 403)
        status, payload, _ = self.request(instance, '/api/auth/login','POST', {'username':'ARTEX','password':'synthetic-test-password'},False)
        self.assertEqual(status, 200); self.assertIn('token', json.loads(payload))
        self.assertEqual(self.request(instance, '/api/skills',token=False)[0],401)
        self.assertTrue(instance.stop().stopped)

    def test_real_authenticated_file_roundtrip_and_mkdir(self):
        instance = self.prepare()
        self.assertEqual(self.request(instance)[0],200)
        self.assertEqual(self.request(instance, '/api/skills/sample/dirs','POST',{'path':'資料'})[0],201)
        path='/api/skills/sample/files/資料/例.txt'.encode().decode('latin-1')
        self.assertEqual(self.request(instance,path,'PUT',{'content':'hello 世界'})[0],200)
        status, payload, headers=self.request(instance,path)
        self.assertEqual(status,200); self.assertEqual(json.loads(payload)['content'],'hello 世界')
        status, empty, head=self.request(instance,path,'HEAD')
        self.assertEqual(status,200);self.assertEqual(empty,b'');self.assertEqual(head['Content-Length'],str(len(payload)))
        self.assertEqual((self.skill/'資料'/'例.txt').read_text(),'hello 世界')
        self.assertEqual(self.request(instance,'/api/skills/sample','DELETE')[0],404)
        self.assertTrue(instance.stop().stopped)

    def test_read_only_profile_does_not_write(self):
        cfg=host.WorkspaceConfig(str(self.root),str(self.password),True)
        instance=self.prepare(cfg)
        self.assertEqual(self.request(instance)[0],200)
        self.assertEqual(self.request(instance,'/api/skills/sample/files/new','PUT',{'content':'x'})[0],403)
        self.assertFalse((self.skill/'new').exists()); self.assertTrue(instance.stop().stopped)

    def test_competing_owner_and_unguarded_release_refused(self):
        instance=self.prepare()
        with self.assertRaises(LeaseUnavailable):self.prepare()
        with self.assertRaises(LeaseUnavailable):instance._lease.release(owner=instance)
        self.assertTrue(instance.stop().stopped)
        again=self.prepare();self.assertTrue(again.stop().stopped)

    def test_bad_root_password_or_missing_declaration_before_lease(self):
        variants=[host.WorkspaceConfig(str(self.root),str(self.password),False),
                  host.WorkspaceConfig(str(self.root),str(self.root/'secret'),True),
                  host.WorkspaceConfig(str(self.root/'..'/'skills'),str(self.password),True)]
        for cfg in variants:
            with patch.object(host.RuntimeLease,'acquire') as acquire:
                with self.assertRaises(ValueError):host.SkillsWorkspaceHost.prepare(cfg)
                acquire.assert_not_called()
        self.password.chmod(0o644)
        with self.assertRaises(ValueError):host._read_password(self.config)
        self.password.chmod(0o600)
        target=self.path/'copy';target.symlink_to(self.password)
        with self.assertRaises(OSError):host._read_password(host.WorkspaceConfig(str(self.root),str(target),True))
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):host._read_password(self.config)

    def test_password_fifo_hardlink_and_oversize_refused(self):
        self.password.unlink();os.mkfifo(self.password,0o600)
        with self.assertRaises(ValueError):host._read_password(self.config)
        self.password.unlink();self.password.write_text('x'*74);self.password.chmod(0o600)
        with self.assertRaises(ValueError):host._read_password(self.config)
        self.password.write_text('synthetic-test-password');os.link(self.password,self.path/'hardlink')
        with self.assertRaises(ValueError):host._read_password(self.config)

    def test_successful_response_tail_retains_request_until_close(self):
        instance=self.prepare(); result=[]
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/skills','QUERY_STRING':'','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(), 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
        body=instance.application(env,lambda *args:result.append(args));next(body)
        self.assertEqual(instance.stop(0).reason,'request_unresolved')
        body.close();self.assertTrue(instance.stop().stopped)
        self.assertEqual(self.request(instance)[0],503)

    def test_inflight_other_thread_can_finish_after_admission_seal(self):
        instance=self.prepare(); entered=threading.Event(); release=threading.Event(); original=instance._application
        def delayed(env,start):
            entered.set();release.wait(2);return original(env,start)
        instance._application=delayed
        receipts=[]; thread=threading.Thread(target=lambda:receipts.append(self.request(instance,'/api/skills/sample/files/new','PUT',{'content':'done'})))
        thread.start();self.assertTrue(entered.wait(2))
        self.assertEqual(instance.stop(0).reason,'request_unresolved')
        release.set();thread.join(3);self.assertFalse(thread.is_alive())
        self.assertEqual(receipts[0][0],200);self.assertTrue(instance.stop().stopped)

    def test_unknown_mutation_permanently_retains_lease_and_seals_admissions(self):
        instance=self.prepare()
        def unknown(env,start):start('500 Internal Server Error',[]);return [b'{"outcome":"unknown"}']
        instance._application=unknown
        self.assertEqual(self.request(instance,'/api/skills/sample/files/new','put',{'content':'x'})[0],500)
        self.assertEqual(instance.stop().reason,'mutation_unknown')
        self.assertIsNotNone(instance._lease._fd)
        self.assertEqual(self.request(instance)[0],503)
        self.assertEqual(self.request(instance,method='head')[:2],(503,b''))
        with self.assertRaises(LeaseUnavailable):RuntimeLease(instance._lease.path).acquire()

    def test_closed_mutation_iterator_is_unknown_not_success(self):
        instance=self.prepare();env={'REQUEST_METHOD':'PUT','PATH_INFO':'/api/skills/sample/files/new','QUERY_STRING':'','CONTENT_LENGTH':'15','wsgi.input':io.BytesIO(b'{"content":"x"}'), 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
        body=instance.application(env,lambda *args:None);next(body);body.close()
        self.assertEqual(instance.stop().reason,'mutation_unknown')

    def test_listener_reuses_hardened_transport_without_binding(self):
        instance=self.prepare();server=Mock();thread=Mock();thread.ident=None
        with patch.object(host,'make_server',return_value=server) as make,patch.object(host.threading,'Thread',return_value=thread):
            self.assertTrue(instance.start())
        self.assertEqual(make.call_args.args[:2],('127.0.0.1',8787))
        self.assertIs(make.call_args.kwargs['server_class'],host._BoundedServer)
        self.assertIs(make.call_args.kwargs['handler_class'],host.QuietRuntimeHandler)
        thread.is_alive.return_value=False;server.quiesce.return_value=True
        self.assertTrue(instance.stop().stopped)

    def test_listener_start_failure_never_returns_success(self):
        instance=self.prepare();server=Mock();thread=Mock();thread.ident=None;thread.start.side_effect=RuntimeError()
        with patch.object(host,'make_server',return_value=server),patch.object(host.threading,'Thread',return_value=thread):
            with self.assertRaises(RuntimeError):instance.start()
        server.server_close.assert_called_once();self.assertTrue(instance.stop().stopped)


    def test_interrupted_thread_start_retains_original_uncertain_launch(self):
        instance=self.prepare();server=Mock();thread=Mock();thread.ident=None;thread.start.side_effect=KeyboardInterrupt()
        with patch.object(host,'make_server',return_value=server),patch.object(host.threading,'Thread',return_value=thread):
            with self.assertRaises(KeyboardInterrupt):instance.start()
        self.assertIs(instance._server,server);self.assertIs(instance._thread,thread)
        thread.is_alive.return_value=False;server.quiesce.return_value=True
        self.assertEqual(instance.stop(0).reason,'launch_unresolved')
        self.assertIsNotNone(instance._lease._fd)

    def test_main_start_error_is_not_a_clean_exit(self):
        instance=Mock();instance.start.side_effect=RuntimeError();instance.stop.return_value=host.WorkspaceStop(True,'closed');instance._closed=True
        args=['--root',str(self.root),'--password-file',str(self.password),'--exclusive-owner']
        with patch.object(host.SkillsWorkspaceHost,'prepare',return_value=instance),patch('sys.stderr',new=io.StringIO()):
            self.assertEqual(host.main(args),1)
        self.assertIsNone(host._signals_module._active_scope)

    def test_real_signal_restore_failure_retains_exact_scope(self):
        class Held(BaseException):pass
        original_signal=signal.signal;original_install=host.RuntimeSignalScope.install
        previous={n:signal.getsignal(n) for n in (signal.SIGINT,signal.SIGTERM)}
        def install(scope):
            original_install(scope);scope.signal_number=signal.SIGTERM
        def signal_call(number,handler):
            if number==signal.SIGTERM and handler is previous[number]:raise OSError('synthetic restore failure')
            return original_signal(number,handler)
        instance=Mock();instance.stop.return_value=host.WorkspaceStop(True,'closed')
        args=['--root',str(self.root),'--password-file',str(self.password),'--exclusive-owner']
        try:
            with patch.object(host.SkillsWorkspaceHost,'prepare',return_value=instance),patch.object(host.RuntimeSignalScope,'install',install),patch.object(signal,'signal',side_effect=signal_call),patch.object(host,'_retain_foreground',side_effect=Held) as retain:
                with self.assertRaises(Held):host.main(args)
            scope=host._signals_module._active_scope
            self.assertIsNotNone(scope);self.assertIs(signal.getsignal(signal.SIGTERM),scope._handler)
            self.assertIn('signal restoration',retain.call_args.args[0])
        finally:
            scope=host._signals_module._active_scope
            if scope is not None:self.assertTrue(scope.restore())

    def test_partial_signal_install_rollback_retains_scope_without_retry(self):
        class Held(BaseException):pass
        original=signal.signal;previous={n:signal.getsignal(n) for n in (signal.SIGINT,signal.SIGTERM)}
        def signal_call(number,handler):
            if number==signal.SIGTERM and handler is not previous[number]:raise OSError('synthetic install failure')
            if number==signal.SIGINT and handler is previous[number]:raise OSError('synthetic rollback failure')
            return original(number,handler)
        args=['--root',str(self.root),'--password-file',str(self.password),'--exclusive-owner']
        try:
            with patch.object(host.SkillsWorkspaceHost,'prepare') as prepare,patch.object(signal,'signal',side_effect=signal_call),patch.object(host,'_retain_foreground',side_effect=Held) as retain,patch('sys.stderr',new=io.StringIO()):
                with self.assertRaises(Held):host.main(args)
            prepare.assert_not_called();scope=host._signals_module._active_scope
            self.assertIsNotNone(scope);self.assertIs(signal.getsignal(signal.SIGINT),scope._handler)
            self.assertIn('installation rollback',retain.call_args.args[0])
        finally:
            scope=host._signals_module._active_scope
            if scope is not None:self.assertTrue(scope.restore())


    def test_unstarted_response_close_has_no_dispatch_or_admission(self):
        instance=self.prepare(); original=instance._application
        instance._application=Mock(wraps=original)
        env={'REQUEST_METHOD':'PUT','PATH_INFO':'/api/skills/sample/files/new','CONTENT_LENGTH':'15','wsgi.input':io.BytesIO(b'{"content":"x"}')}
        response=instance.application(env,lambda *args:None)
        response.close()
        instance._application.assert_not_called();self.assertFalse(instance._active)
        self.assertFalse((self.skill/'new').exists());self.assertTrue(instance.stop().stopped)


    def _run_stopped_main(self, instance, retain):
        original=host.RuntimeSignalScope.install
        def install(scope):original(scope);scope.signal_number=signal.SIGTERM
        args=['--root',str(self.root),'--password-file',str(self.password),'--exclusive-owner']
        with patch.object(host.SkillsWorkspaceHost,'prepare',return_value=instance),patch.object(host.RuntimeSignalScope,'install',install),patch.object(host,'_retain_foreground',side_effect=retain):
            host.main(args)

    def test_original_stop_wait_interruption_retains_active_request_host_and_signals(self):
        class Held(BaseException):pass
        instance=self.prepare();entered=threading.Event();release=threading.Event()
        def request_tail():
            env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/skills','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(), 'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(instance._auth.key)}
            response=instance.application(env,lambda *args:None)
            try:next(response);entered.set();release.wait(3)
            finally:response.close()
        thread=threading.Thread(target=request_tail);thread.start();self.assertTrue(entered.wait(2))
        try:
            with patch.object(instance._state,'wait_for',side_effect=KeyboardInterrupt) as wait:
                with self.assertRaises(Held):self._run_stopped_main(instance,Held)
            wait.assert_called_once();self.assertEqual(len(instance._active),1)
            self.assertIsNotNone(instance._lease._fd);self.assertFalse(instance._closed)
            self.assertIsNotNone(host._signals_module._active_scope)
        finally:
            release.set();thread.join(3);self.assertFalse(thread.is_alive())
            scope=host._signals_module._active_scope
            if scope is not None:self.assertTrue(scope.restore())
        self.assertTrue(instance.stop().stopped)  # Explicit test reconciliation only.

    def test_original_pre_release_interruption_retains_scope_and_does_not_retry(self):
        class Held(BaseException):pass
        instance=self.prepare()
        try:
            with patch.object(instance,'_check',side_effect=SystemExit) as check:
                with self.assertRaises(Held):self._run_stopped_main(instance,Held)
            check.assert_called_once();self.assertIsNotNone(instance._lease._fd);self.assertFalse(instance._closed)
            self.assertIsNotNone(host._signals_module._active_scope)
        finally:
            scope=host._signals_module._active_scope
            if scope is not None:self.assertTrue(scope.restore())
        self.assertTrue(instance.stop().stopped)

    def test_original_false_stop_retains_host_and_signal_scope(self):
        class Held(BaseException):pass
        instance=self.prepare();instance._quarantine()
        try:
            with self.assertRaises(Held):self._run_stopped_main(instance,Held)
            self.assertIsNotNone(instance._lease._fd);self.assertFalse(instance._closed)
            self.assertIsNotNone(host._signals_module._active_scope)
        finally:
            scope=host._signals_module._active_scope
            if scope is not None:self.assertTrue(scope.restore())


    def test_original_unknown_stop_retains_even_when_stderr_pipe_is_broken(self):
        class Held(BaseException):pass
        instance=self.prepare();instance._quarantine()
        read_fd,write_fd=os.pipe();os.close(read_fd)
        stream=os.fdopen(write_fd,'w',buffering=1)
        original=host.RuntimeSignalScope.install
        def install(scope):original(scope);scope.signal_number=signal.SIGTERM
        args=['--root',str(self.root),'--password-file',str(self.password),'--exclusive-owner']
        try:
            with patch.object(host.SkillsWorkspaceHost,'prepare',return_value=instance),patch.object(host.RuntimeSignalScope,'install',install),patch('sys.stderr',stream),patch.object(host.time,'sleep',side_effect=Held) as sleep:
                with self.assertRaises(Held):host.main(args)
            sleep.assert_called_once_with(1)
            self.assertTrue(instance._unknown);self.assertIsNotNone(instance._lease._fd)
            self.assertIsNotNone(host._signals_module._active_scope)
        finally:
            try:stream.close()
            except BrokenPipeError:pass
            scope=host._signals_module._active_scope
            if scope is not None:self.assertTrue(scope.restore())


if __name__=='__main__':unittest.main()
