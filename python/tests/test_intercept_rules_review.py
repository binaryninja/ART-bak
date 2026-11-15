"""Independent metadata authority and transaction tests, inert executor only."""
import unittest
from contextlib import contextmanager
import test_intercept_rules as fixtures
from artex.db.intercept_rules import RuleDenied,RuleUncertain,RuleUnavailable,RuleProposal

class Review(unittest.TestCase):
    make=fixtures.RuleTests.make
    def test_idle_callback_revocation_before_transaction(self):
        store=self.make([]);live=[True]
        store.current=lambda scope:live[0]
        def idle(scope):live[0]=False;return True
        store.idle=idle
        with self.assertRaises(RuleDenied):store.create(RuleProposal('exact_domain','a.test'))
        self.assertEqual(self.db.calls,[])
    def test_postcommit_authority_loss_reports_committed_uncertainty(self):
        store=self.make([[(5,)],[(7,)],[fixtures.ROW]]);live=[True]
        store.current=lambda scope:live[0];original=self.db.transaction
        @contextmanager
        def transaction():
            with original() as tx:yield tx
            live[0]=False
        self.db.transaction=transaction
        with self.assertRaises(RuleUncertain):store.create(RuleProposal('exact_domain','a.test'))
        self.assertEqual(self.db.calls[-1][0],'commit')
    def test_global_write_requires_explicit_whole_owner_idle(self):
        store=self.make([],task=False);store.idle=None
        with self.assertRaises(RuleDenied):store.create(RuleProposal('exact_domain','a.test'))
        self.assertEqual(self.db.calls,[])
    def test_note_overflow_sentinel_fails_without_partial_list(self):
        row=list(fixtures.ROW);row[4]=None
        store=self.make([[(5,)],[row]])
        with self.assertRaises(RuleUnavailable):store.list()
        self.assertEqual(self.db.calls[-1][0],'rollback')
    def test_global_allow_rejected_before_authorization_or_write(self):
        store=self.make([],task=False)
        with self.assertRaises(ValueError):store.create(RuleProposal('exact_domain','a.test',action='allow'))
        self.assertEqual(self.db.calls,[])

if __name__=='__main__':unittest.main()
