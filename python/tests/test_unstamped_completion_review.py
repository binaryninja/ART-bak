"""Independent synthetic prepared-owner review; no dispatch or positive admission."""
import unittest
from contextlib import contextmanager
from dataclasses import replace
import test_unstamped_completion as fixture
from artex.db.unstamped_completion import UnstampedCompletionConflict, UnstampedCompletionUncertain, FIELDS

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_copied_cap_and_foreign_handle_cannot_validate(self):
        f=self.f;cap=f.manager.reserve_unstamped_completion(f.store)
        self.assertTrue(f.manager.validate_unstamped_completion(cap,f.store))
        self.assertFalse(f.manager.validate_unstamped_completion(replace(cap),f.store))
        original=f.d.handle;f.d.handle=object()
        self.assertFalse(f.manager.validate_unstamped_completion(cap,f.store))
        f.d.handle=original
        self.assertFalse(f.manager.owns_generation('5',original.token,original))
        self.assertFalse(f.queries)

    def test_commit_exit_failure_retains_owner_without_fabricating_clock(self):
        f=self.f;old=f.d.executor.transaction
        @contextmanager
        def transaction():
            with old() as tx:yield tx
            raise RuntimeError('synthetic acknowledgement loss')
        f.d.executor.transaction=transaction
        with self.assertRaises(UnstampedCompletionUncertain):f.store.settle()
        self.assertEqual(f.status,'done');self.assertIsNotNone(f.saved)
        self.assertEqual(f.clocks,(None,None));self.assertIsNone(f.d.lease)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        self.assertEqual(f.manager.lifecycle_snapshot('5')['status'],'running')
        with self.assertRaises(UnstampedCompletionConflict):f.store.settle()

    def test_final_grant_partial_clock_drift_is_not_retired_or_cleared(self):
        f=self.f;calls=[]
        def grant(receipt,operation):
            if operation=='write':
                calls.append(operation)
                if len(calls)==5:f.clocks=(f.completed,None)
            return f.permit
        from artex.db.unstamped_completion import UnstampedCompletionStore
        f.store=UnstampedCompletionStore(f.d,grant=f.permit,authorize=grant)
        with self.assertRaises(UnstampedCompletionUncertain):f.store.settle()
        self.assertEqual(f.clocks,(f.completed,None));self.assertIsNone(f.d.lease)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')

    def test_last_frontier_read_revokes_permit_after_receipt_read(self):
        f=self.f;old=f.d.executor.fetch_all;reads=[];frontiers=[]
        def fetch(query):
            value=old(query)
            if query.sql.startswith('SELECT '+FIELDS+' FROM public.artex_unstamped_completion'):reads.append(query)
            if len(reads)==4 and query.sql.startswith('SELECT id,kind,CASE'):
                frontiers.append(query);f.permit.revoke()
            return value
        f.d.executor.fetch_all=fetch
        with self.assertRaises(UnstampedCompletionUncertain):f.store.settle()
        self.assertEqual(len(frontiers),1)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        self.assertEqual(f.manager.lifecycle_snapshot('5')['status'],'running')
