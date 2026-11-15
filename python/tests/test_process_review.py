"""Independent fake-only lifecycle review; never spawn commands."""
import unittest
from artex.tools.process import CleanupUncertain,PosixRunner,Launch
from test_process import Context,FakeProcess,FakeSelector

class Interrupted(BaseException):pass

class ProcessReview(unittest.TestCase):
    def test_cleanup_baseexception_still_reaps_and_closes(self):
        proc=FakeProcess();selector=FakeSelector()
        def interrupted(pid,sig):raise Interrupted('private')
        runner=PosixRunner(popen=lambda *a,**k:proc,selector=lambda:selector,killpg=interrupted,read=lambda *a:b'',clock=lambda:0)
        with self.assertRaises(CleanupUncertain):runner(Context(),Launch('fixture','/fixture','/bin/bash',(),1000,4))
        self.assertEqual(len(proc.waits),1);self.assertTrue(proc.stdout.closed);self.assertTrue(selector.closed)
    def test_reap_baseexception_still_closes_stream_selector(self):
        proc=FakeProcess();selector=FakeSelector()
        def interrupted(timeout):raise Interrupted('private')
        proc.wait=interrupted
        runner=PosixRunner(popen=lambda *a,**k:proc,selector=lambda:selector,killpg=lambda *a:None,read=lambda *a:b'',clock=lambda:0)
        with self.assertRaises(CleanupUncertain):runner(Context(),Launch('fixture','/fixture','/bin/bash',(),1000,4))
        self.assertTrue(proc.stdout.closed);self.assertTrue(selector.closed)
    def test_selector_registration_failure_reaps_spawned_leader(self):
        proc=FakeProcess();selector=FakeSelector();kills=[]
        def fail(*a):raise ValueError('private')
        selector.register=fail
        runner=PosixRunner(popen=lambda *a,**k:proc,selector=lambda:selector,killpg=lambda *a:kills.append(a),clock=lambda:0)
        from artex.tools.process import ProcessError
        with self.assertRaises(ProcessError):runner(Context(),Launch('fixture','/fixture','/bin/bash',(),1000,4))
        self.assertTrue(kills and proc.waits and proc.stdout.closed and selector.closed)
