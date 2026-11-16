"""Independent scoped persistence review, inert transactional executor only."""
import unittest
from dataclasses import replace
from artex.db.intercept_decisions import *
import test_intercept_decisions as base

class InterceptDecisionsReview(unittest.TestCase):
    def setUp(self):self.f=base.DecisionStoreTests()
    def test_final_lifetime_callback_revokes_write_before_insert(self):
        allowed=[True];calls=[]
        def current(scope):
            calls.append(1)
            if len(calls)==6:allowed[0]=False
            return True
        s=self.f.make([[(5,)],[(20,)]],current=current,write=lambda *a:allowed[0])
        with self.assertRaises(DecisionDenied):s.create(DecisionRecord('Bash'))
        self.assertFalse(any(isinstance(q,Query) and q.sql.startswith('INSERT') for q in self.f.db.calls))
    def test_scope_type_drift_after_parent_read_refuses_write(self):
        s=self.f.make([[(5,)],[(20,)]])
        self.f.db.on_query=lambda q:object.__setattr__(self.f.scope,'exploration_id',9.0)
        with self.assertRaises(DecisionDenied):s.create(DecisionRecord('Bash'))
        self.assertFalse(any(isinstance(q,Query) and q.sql.startswith('INSERT') for q in self.f.db.calls))
    def test_read_cannot_cross_xor_parent_or_numeric_equivalence(self):
        for conversation,task in ((7,'5'),(7,None),(True,None)):
            s=self.f.make([[(5,)],[(20,None,conversation,task,'','Bash','{}','pending','',None,base.NOW,'unknown',None,30)]])
            with self.assertRaises(DecisionUnavailable):s.get(20)
    def test_resolution_invalid_count_is_sticky_uncertainty(self):
        for count in (True,2,-1):
            s=self.f.make([[(5,)]])
            self.f.db.execute=lambda q,count=count:self.f.db.calls.append(q) or count
            with self.assertRaises(DecisionUncertain):s.resolve(DecisionResolution(20,'allowed','allow','ok'))
            n=len(self.f.db.calls)
            with self.assertRaises(DecisionUncertain):s.get(20)
            self.assertEqual(n,len(self.f.db.calls))
    def test_request_callback_mutation_of_record_rejects_before_write(self):
        s=self.f.make([[(5,)],[(20,)]]);r=DecisionRecord('Bash')
        def grant(*args):object.__setattr__(r,'status','allowed');return True
        with self.assertRaises(DecisionDenied):s.create(r,request_authorize=grant)
        self.assertFalse(any(isinstance(q,Query) and q.sql.startswith('INSERT') for q in self.f.db.calls))
    def test_audit_null_not_fabricated_and_unknown_correlation_not_promoted(self):
        s=self.f.make([[(7,)]],conversation=True)
        self.assertTrue(s.resolve(DecisionResolution(20,'timeout','allow','manual')))
        q=[q for q in self.f.db.calls if isinstance(q,Query)][-1]
        self.assertEqual(q.params[1],7);self.assertIn('task_id IS NULL',q.sql)
        self.assertIn('audit IS NULL THEN NULL',q.sql);self.assertIn("IS DISTINCT FROM 'exact'",q.sql)
        self.assertNotIn('run_id',q.params[3]);self.assertNotIn('tool_use_id',q.params[3])
