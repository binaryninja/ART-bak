"""Independent real process ledger/frontier composition, inert callbacks only."""
import tempfile,unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from artex.tools.process import ProcessResult
import frontier_process_fixture as fixture

class ProcessFrontierReview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)

    def test_invalid_process_receipt_survives_cancel_and_cannot_retire(self):
        calls=[]
        def runner(c,l):calls.append(l);return ProcessResult(b'',True)
        f,b=fixture.make(self,self.tmp.name,runner)
        with self.assertRaises(Exception):f.host.advance_owned(lambda:f.host.runner.advance('5',f.handle.token,1))
        self.assertTrue(f.handle._child.process.unresolved())
        f.handle.token.cancel('paused_by_user')
        for _ in range(2):
            with self.assertRaises(Exception):f.host.poll()
            self.assertIn(f.handle,f.host.runner.handles())
            self.assertIsNone(f.component._store)
            self.assertIsNone(f.f.saved)
            self.assertFalse(f.handle.wait(0))
        self.assertEqual(len(calls),1)
        self.assertEqual(f.f.status,'running')

    def test_actual_process_return_allows_only_following_owned_poll(self):
        entered=Event();release=Event();calls=[]
        def runner(c,l):
            calls.append(l);entered.set()
            if not release.wait(3):raise RuntimeError('inert test timeout')
            return ProcessResult(b'finished',0)
        f,b=fixture.make(self,self.tmp.name,runner)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(f.host.advance_owned,lambda:f.host.runner.advance('5',f.handle.token,1))
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaises(Exception):f.host.runner.settle_completed(f.host.manager.complete)
                self.assertTrue(f.host.poll().busy)
                self.assertFalse(f.host.drain(0).workers_drained)
                self.assertIn(f.handle,f.host.runner.handles())
                self.assertIsNone(f.f.saved)
            finally:release.set()
            pending.result(1)
        self.assertTrue(f.handle._child.process.drained())
        self.assertTrue(f.host.poll().all_settled)
        self.assertNotIn(f.handle,f.host.runner.handles())
        self.assertEqual(f.f.status,'done')
        self.assertEqual(len(calls),1)
        self.assertTrue(f.owner.snapshot().drained)
