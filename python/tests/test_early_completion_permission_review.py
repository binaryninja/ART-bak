"""Independent permit boundaries; inert SQL and actual installed owner only."""
import unittest
from threading import Thread, Event
from artex.db.early_completion import EarlyCompletionStore, EarlyCompletionGrant, EarlyCompletionConflict, EarlyCompletionUncertain
import test_early_completion as fixture

class PermissionReview(unittest.TestCase):
    def setup_fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f

    def test_last_sql_read_revocation_retains_committed_owner(self):
        f=self.setup_fixture();original=f.d.executor.fetch_all;frontiers=[]
        def fetch(query):
            result=original(query)
            if query.sql.startswith('SELECT id,kind,CASE WHEN octet_length(state)'):
                frontiers.append(True)
                if len(frontiers)==6:f.permit.revoke()
            return result
        f.d.executor.fetch_all=fetch
        with self.assertRaises(EarlyCompletionUncertain):f.store.settle()
        self.assertEqual(len(frontiers),6)
        self.assertEqual(f.status,'done');self.assertIsNotNone(f.saved)
        self.assertEqual(f.manager.lifecycle_snapshot('5')['status'],'running')
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        self.assertFalse(f.d._token._event.is_set())

    def test_cancel_is_after_permit_and_manager_locks_release(self):
        f=self.setup_fixture();original=f.d._token.cancel;observed=[]
        def cancel(reason):
            finished=Event()
            def probe():
                f.permit.revoke()
                observed.append(f.manager.lifecycle_snapshot('5')['status'])
                finished.set()
            thread=Thread(target=probe,daemon=True);thread.start()
            if not finished.wait(1):raise AssertionError('Cancellation called under acknowledgement or manager lock')
            thread.join(1);original(reason)
        f.d._token.cancel=cancel
        self.assertEqual(f.store.settle().status,'done')
        self.assertEqual(observed,['done']);self.assertTrue(f.d._token._event.is_set())
        with self.assertRaises(EarlyCompletionConflict):f.permit._validate(f.d.receipt,'write')

    def test_sql_and_policy_callbacks_not_inside_acknowledgement(self):
        f=self.setup_fixture();original=f.d.executor.fetch_all;checked=[]
        def probe():
            done=Event()
            def other():
                f.permit._validate(f.d.receipt,'read')
                f.manager.lifecycle_snapshot('5');done.set()
            thread=Thread(target=other,daemon=True);thread.start()
            if not done.wait(1):raise AssertionError('External callback inside acknowledgement/manager lock')
            thread.join(1);checked.append(True)
        def fetch(query):probe();return original(query)
        def authorize(*args):probe();return f.permit
        f.d.executor.fetch_all=fetch
        store=EarlyCompletionStore(f.d,grant=f.permit,authorize=authorize)
        self.assertEqual(store.settle().status,'done');self.assertGreater(len(checked),10)

    def test_cancellation_failure_retains_committed_uncertainty_without_retry(self):
        f=self.setup_fixture();calls=[]
        def cancel(reason):
            calls.append(reason)
            raise RuntimeError('private cancellation failure')
        f.d._token.cancel=cancel
        with self.assertRaises(EarlyCompletionUncertain) as error:f.store.settle()
        self.assertNotIn('private cancellation failure',str(error.exception))
        self.assertEqual(f.status,'done');self.assertIsNotNone(f.saved)
        self.assertEqual(f.manager.lifecycle_snapshot('5')['status'],'done')
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        count=len(f.queries)
        with self.assertRaises(EarlyCompletionConflict):f.store.settle()
        self.assertEqual(len(f.queries),count);self.assertEqual(calls,['frontier_drained'])
        # Failed cancellation must not leave the permit acknowledgement held.
        finished=Event()
        thread=Thread(target=lambda:(f.permit.revoke(),finished.set()),daemon=True)
        thread.start();self.assertTrue(finished.wait(1));thread.join(1)

    def test_read_only_permit_cannot_retire_or_write(self):
        f=self.setup_fixture()
        permit=EarlyCompletionGrant(f.d.receipt,operations=frozenset({'reserve','read'}))
        store=EarlyCompletionStore(f.d,grant=permit,authorize=lambda *args:permit)
        with self.assertRaises(EarlyCompletionConflict):store.settle()
        self.assertEqual(f.status,'running');self.assertIsNone(f.saved)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')

if __name__=='__main__':unittest.main()
