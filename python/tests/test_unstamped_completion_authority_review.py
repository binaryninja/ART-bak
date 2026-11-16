"""Exact unstamped reservation and retirement concurrency, no model/process."""
import unittest
from dataclasses import replace
from threading import Thread,Event
from artex.manager import TaskConflict
from artex.db.unstamped_completion import UnstampedCompletionConflict,UnstampedCompletionUncertain
import test_unstamped_completion as fixture

class AuthorityReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f

    def test_wait_outside_manager_lock_with_execution_already_fenced(self):
        f=self.fixture();entered=Event();release=Event();outcome=[]
        def wait(timeout):entered.set();return release.wait(2)
        f.d.handle.wait=wait
        def reserve():
            try:outcome.append(f.manager.reserve_unstamped_completion(f.store))
            except BaseException as exc:outcome.append(exc)
        thread=Thread(target=reserve,daemon=True);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            checked=Event();observed=[]
            def inspect():
                observed.append(f.manager.owns_generation('5',f.d._token,f.d.handle))
                try:f.manager.reserve_deadline_owner('5',f.d._token,f.d.handle,store=f.d,timeout_seconds=10)
                except TaskConflict:observed.append('stamp denied')
                checked.set()
            other=Thread(target=inspect,daemon=True);other.start()
            self.assertTrue(checked.wait(1));other.join(1)
            self.assertEqual(observed,[False,'stamp denied'])
        finally:release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(len(outcome),1)
        self.assertTrue(f.manager.validate_unstamped_completion(outcome[0],f.store))
        self.assertFalse(f.queries);self.assertIsNone(f.d.lease)

    def test_copied_cap_cannot_validate_or_quarantine_original(self):
        f=self.fixture();cap=f.manager.reserve_unstamped_completion(f.store)
        duplicate=replace(cap)
        self.assertFalse(f.manager.validate_unstamped_completion(duplicate,f.store))
        with self.assertRaises(TaskConflict):f.manager.quarantine_unstamped_completion(duplicate,f.store)
        self.assertTrue(f.manager.validate_unstamped_completion(cap,f.store))
        with self.assertRaises(TaskConflict):f.manager.control_task('5','resume')

    def test_cancel_failure_retains_committed_null_clock_without_retry(self):
        f=self.fixture();calls=[]
        def cancel(reason):calls.append(reason);raise RuntimeError('private failure')
        f.d._token.cancel=cancel
        with self.assertRaises(UnstampedCompletionUncertain) as err:f.store.settle()
        self.assertNotIn('private failure',str(err.exception))
        self.assertEqual(f.status,'done');self.assertEqual(f.clocks,(None,None));self.assertIsNone(f.d.lease)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        count=len(f.queries)
        with self.assertRaises(UnstampedCompletionConflict):f.store.settle()
        self.assertEqual(len(f.queries),count);self.assertEqual(calls,['frontier_drained'])

    def test_cancel_after_ack_lock_and_registry_release(self):
        f=self.fixture();original=f.d._token.cancel;observed=[]
        def cancel(reason):
            done=Event()
            def inspect():
                f.permit.revoke();observed.append(f.manager.lifecycle_snapshot('5')['status']);done.set()
            thread=Thread(target=inspect,daemon=True);thread.start()
            if not done.wait(1):raise AssertionError('Cancellation under retirement locks')
            thread.join(1);original(reason)
        f.d._token.cancel=cancel
        self.assertEqual(f.store.settle().status,'done');self.assertEqual(observed,['done'])

if __name__=='__main__':unittest.main()
