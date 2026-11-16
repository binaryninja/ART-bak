"""Explicit local lease and inert listener/session lifecycle tests."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from artex.runtime_lease import RuntimeLease, LeaseUnavailable
from artex.server.runtime_session import RuntimeSession
from artex.server.runtime_listener import RuntimeListener, ListenerStop
from artex.agent.host import DrainResult, PollResult
import test_runtime_listener as fixtures


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'runtime.lock')
        self.lease = RuntimeLease(self.path)
        self.addCleanup(self.lease.release)

    def test_acquire_contend_release_preserves_inode(self):
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(self.lease.acquire())
        self.assertFalse(self.lease.acquire())
        inode = os.stat(self.path).st_ino
        other = RuntimeLease(self.path)
        with self.assertRaises(LeaseUnavailable): other.acquire()
        self.assertTrue(self.lease.validate())
        self.assertTrue(self.lease.release())
        self.assertEqual(os.stat(self.path).st_ino, inode)
        self.assertTrue(other.acquire()); other.release()

    def test_real_separate_process_contention(self):
        self.lease.acquire()
        code = 'import fcntl,sys; f=open(sys.argv[1],"r+"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
        env = {'PATH':os.environ.get('PATH','')}
        blocked = subprocess.run([sys.executable,'-c',code,self.path],env=env,capture_output=True,timeout=5)
        self.assertNotEqual(blocked.returncode,0)
        self.assertIn(b'BlockingIOError',blocked.stderr)
        self.lease.release()
        free = subprocess.run([sys.executable,'-c',code,self.path],env=env,capture_output=True,timeout=5)
        self.assertEqual(free.returncode,0)

    def test_storage_and_replacement_refused(self):
        target = Path(self.tmp.name)/'target'; target.write_text('do not change')
        os.symlink(target,self.path)
        with self.assertRaises(LeaseUnavailable): self.lease.acquire()
        self.assertEqual(target.read_text(),'do not change')
        os.unlink(self.path)
        Path(self.path).write_text('preserved'); os.chmod(self.path,0o644)
        with self.assertRaises(LeaseUnavailable): self.lease.acquire()
        os.chmod(self.path,0o600); self.lease.acquire()
        os.rename(self.path,self.path+'.old'); Path(self.path).write_text('replacement')
        with self.assertRaises(LeaseUnavailable): self.lease.validate()
        self.lease.release()
        self.assertTrue(Path(self.path+'.old').exists())

    def test_fork_identity_and_bad_directory(self):
        self.lease.acquire()
        with patch('artex.runtime_lease.os.getpid',return_value=-1):
            with self.assertRaises(LeaseUnavailable): self.lease.validate()
            with self.assertRaises(LeaseUnavailable): self.lease.release()
        self.lease.release()
        os.chmod(self.tmp.name,0o777)
        with self.assertRaises(LeaseUnavailable): self.lease.acquire()
        os.chmod(self.tmp.name,0o700)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.lease=RuntimeLease(str(Path(self.tmp.name)/'runtime.lock')); self.lease.acquire()
        self.addCleanup(self.lease.release)
        self.fixture=fixtures.Tests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.listener=RuntimeListener(self.fixture.bundle,self.fixture.owner)
        self.session=RuntimeSession(self.listener,self.lease)
        self.addCleanup(self.session.stop,1)

    def test_inert_creation_and_explicit_order_idempotency(self):
        self.assertEqual(self.fixture.calls,[])
        calls=[]
        with patch.object(self.listener,'start_listener',side_effect=lambda:calls.append('listener') or True), patch.object(self.listener,'start_pump',side_effect=lambda:calls.append('pump') or True):
            self.assertTrue(self.session.start().started)
            self.assertTrue(self.session.start().started)
        self.assertEqual(calls,['listener','pump'])
        self.assertTrue(self.session.stop(1).stopped)
        self.assertEqual(self.fixture.calls,['admissions','cancel','resources'])
        self.assertTrue(self.session.stop(0).stopped)
        self.assertFalse(self.session.start().started)
        with self.assertRaises(LeaseUnavailable): self.lease.validate()

    def test_one_session_claim_and_no_early_release(self):
        with self.assertRaises(LeaseUnavailable): RuntimeSession(self.listener,self.lease)
        with self.assertRaises(LeaseUnavailable): self.lease.release()
        self.assertTrue(self.lease.validate())
        self.assertTrue(self.session.stop(1).stopped)

    def test_partial_start_failure_drains(self):
        with patch.object(self.listener,'start_listener',return_value=True), patch.object(self.listener,'start_pump',side_effect=RuntimeError('private')):
            result=self.session.start()
        self.assertFalse(result.started); self.assertTrue(result.stopped)
        self.assertEqual(result.reason,'startup_failed')
        self.assertEqual(self.fixture.calls,['admissions','cancel','resources'])
        with self.assertRaises(LeaseUnavailable): self.lease.validate()

    def test_failed_start_unresolved_retains_lease_then_explicit_stop(self):
        with patch.object(self.listener,'start_listener',side_effect=OSError()), patch.object(self.fixture.bundle.host,'drain',return_value=DrainResult(False,PollResult(True))):
            result=self.session.start(shutdown_timeout=0)
        self.assertFalse(result.stopped); self.assertEqual(result.reason,'startup_failed_unresolved')
        self.assertTrue(self.lease.validate()); self.assertNotIn('resources',self.fixture.calls)
        self.assertFalse(self.session.start().started)
        self.assertTrue(self.session.stop(1).stopped)

    def test_stop_exception_and_untrusted_result_keep_lease(self):
        for effect in (RuntimeError(), None):
            with patch.object(self.listener,'stop',side_effect=effect,return_value=True):
                self.assertFalse(self.session.stop(0).stopped)
            self.assertTrue(self.lease.validate())
        self.assertTrue(self.session.stop(1).stopped)

    def test_reentrant_stop_is_busy_without_releasing(self):
        def start():
            self.assertEqual(self.session.stop(0).reason,'session_busy')
            self.assertTrue(self.lease.validate())
            return True
        with patch.object(self.listener,'start_listener',side_effect=start), patch.object(self.listener,'start_pump',return_value=True):
            self.assertTrue(self.session.start().started)
        self.session.stop(1)

    def test_missing_lease_started_listener_and_invalid_timeout(self):
        for timeout in (True,-1,301,float('nan')):
            with self.assertRaises(ValueError): self.session.start(shutdown_timeout=timeout)
        self.assertEqual(self.fixture.calls,[])
        self.listener._stopping=True
        with self.assertRaises(ValueError): RuntimeSession(self.listener,self.lease)
        self.listener._stopping=False
        self.session.stop(1)
        with self.assertRaises(LeaseUnavailable): RuntimeSession(self.listener,self.lease)
