import tempfile,unittest
from threading import Event,Thread
from artex.tools.process import ProcessResult
import frontier_process_fixture as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def test_actual_process_uncertainty_prevents_frontier_write(self):
        calls=[]
        def runner(c,l):calls.append(l);raise RuntimeError('inert uncertainty')
        f,b=fixture.make(self,self.tmp.name,runner)
        with self.assertRaises(Exception):f.host.advance_owned(lambda:f.host.runner.advance('5',f.handle.token,1))
        self.assertEqual(len(calls),1)
        self.assertTrue(f.handle._child.process.unresolved());self.assertFalse(f.handle.wait(0))
        with self.assertRaises(Exception):f.host.poll()
        self.assertIsNone(f.component._store);self.assertIn(f.handle,f.host.runner.handles())
        self.assertIsNone(f.f.saved);self.assertEqual(f.f.status,'running')
    def test_active_process_keeps_poll_busy(self):
        entered=Event();release=Event();errors=[]
        def runner(c,l):entered.set();release.wait(3);return ProcessResult(b'ok',0)
        f,b=fixture.make(self,self.tmp.name,runner)
        def advance():
            try:f.host.advance_owned(lambda:f.host.runner.advance('5',f.handle.token,1))
            except BaseException as exc:errors.append(exc)
        thread=Thread(target=advance);thread.start()
        try:
            self.assertTrue(entered.wait(1));self.assertTrue(f.host.poll().busy)
            self.assertFalse(f.host.drain(0).all_settled);self.assertFalse(f.handle._child.process.drained())
            self.assertIsNone(f.f.saved)
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
