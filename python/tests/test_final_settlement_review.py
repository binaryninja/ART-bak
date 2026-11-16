"""Independent terminal persistence tests; inert SQL, no positive admission."""
import unittest
from artex.db.final_settlement import FinalSettlementStore,SettlementUncertain,SettlementConflict
import test_final_settlement as fixture

class SettlementReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_postcommit_task_status_drift_cannot_retire_as_done(self):
        f=self.f;calls=[]
        def approve(receipt,operation,status):
            if operation=='write':
                calls.append(True)
                if len(calls)==3:f.status='failed'
            return True
        store=FinalSettlementStore(f.f.journal,f.receipt,authorize=approve)
        with self.assertRaises(SettlementUncertain):store.settle()
        self.assertEqual(f.status,'failed')
        self.assertEqual(f.f.manager.deadline_owners()[0].state,'quarantined')
    def test_postcommit_settlement_receipt_loss_cannot_retire(self):
        f=self.f;calls=[]
        def approve(receipt,operation,status):
            if operation=='write':
                calls.append(True)
                if len(calls)==3:f.saved=None
            return True
        store=FinalSettlementStore(f.f.journal,f.receipt,authorize=approve)
        with self.assertRaises(SettlementUncertain):store.settle()
        self.assertEqual(f.f.manager.deadline_owners()[0].state,'quarantined')
    def test_unconfirmed_local_proof_is_not_model_completion(self):
        f=self.f;f.nodes=[(1,'goal','met'),(2,'fact','dismissed')]
        with self.assertRaises(SettlementConflict):f.store.settle()
        self.assertEqual(f.status,'running');self.assertIsNone(f.saved)
    def test_retirement_cancel_failure_retains_quarantine_after_commit(self):
        f=self.f
        def fail(reason):raise RuntimeError('private cancellation failure')
        f.f.handle.token.cancel=fail
        with self.assertRaises(SettlementUncertain) as error:f.store.settle()
        self.assertNotIn('private',str(error.exception))
        self.assertEqual(f.status,'done');self.assertIsNotNone(f.saved)
        self.assertEqual(f.f.manager.deadline_owners()[0].state,'quarantined')
        with self.assertRaises(SettlementConflict):f.store.settle()
    def test_last_policy_callback_final_journal_drift_blocks_retirement(self):
        from dataclasses import replace
        f=self.f;calls=[]
        def approve(receipt,operation,status):
            if operation=='write':
                calls.append(True)
                if len(calls)==5:f.f.row=replace(f.f.row,revision=f.f.row.revision+1)
            return True
        store=FinalSettlementStore(f.f.journal,f.receipt,authorize=approve)
        with self.assertRaises(SettlementUncertain):store.settle()
        self.assertEqual(f.status,'done')
        self.assertEqual(f.f.manager.deadline_owners()[0].state,'quarantined')
