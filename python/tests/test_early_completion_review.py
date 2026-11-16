"""Independent actual-manager/receipt fixtures; no positive admission enabled.

Revocation actions migrated intentionally from the unpublished boolean policy to
the exact scoped permit API. Third/fourth SQL boundaries and assertions remain.
"""
import unittest
from dataclasses import replace
from artex.db.early_completion import EarlyCompletionUncertain,EarlyCompletionConflict,FIELDS
import test_early_completion as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_final_readback_revokes_write_grant_retains_owner(self):
        f=self.f;original=f.d.executor.fetch_all;reads=[]
        def query(q):
            result=original(q)
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_early_completion'):
                reads.append(q)
                if len(reads)==3:f.permit.revoke()
            return result
        f.d.executor.fetch_all=query
        with self.assertRaises(EarlyCompletionUncertain):f.store.settle()
        self.assertEqual(len(reads),3)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
    def test_new_final_fourth_readback_revokes_grant(self):
        f=self.f;original=f.d.executor.fetch_all;reads=[]
        def query(q):
            result=original(q)
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_early_completion'):
                reads.append(q)
                if len(reads)==4:f.permit.revoke()
            return result
        f.d.executor.fetch_all=query
        with self.assertRaises(EarlyCompletionUncertain):f.store.settle()
        self.assertEqual(len(reads),4)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
    def test_postcommit_lost_receipt_keeps_done_unacknowledged(self):
        f=self.f;original=f.d.executor.fetch_all;reads=[]
        def query(q):
            result=original(q)
            if q.sql.startswith('SELECT '+FIELDS+' FROM public.artex_early_completion'):
                reads.append(q)
                if len(reads)==2:return []
            return result
        f.d.executor.fetch_all=query
        with self.assertRaises(EarlyCompletionUncertain):f.store.settle()
        self.assertEqual(f.status,'done');self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        with self.assertRaises(EarlyCompletionConflict):f.store.settle()
    def test_cap_copy_does_not_validate(self):
        f=self.f;cap=f.manager.reserve_early_completion(f.store)
        self.assertTrue(f.manager.validate_early_completion(cap,f.store))
        self.assertFalse(f.manager.validate_early_completion(replace(cap),f.store))
    def test_source_settled_states_need_no_proof_rows(self):
        f=self.f;f.nodes=[(1,'goal','abandoned'),(2,'goal','met'),(3,'intent','paused')]
        result=f.store.settle();self.assertEqual(result.reason,'frontier_drained')
        self.assertFalse(any('proves' in q.sql or 'payload' in q.sql for q in f.queries))
