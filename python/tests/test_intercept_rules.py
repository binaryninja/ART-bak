import unittest
from contextlib import nullcontext
from artex.db.intercept_rules import *
from test_task_categories import Fake,NOW
ROW=(7,True,'exact_domain','example.test','note',False,'block',NOW,NOW)

class RuleTests(unittest.TestCase):
    def make(self,results,task=True,**kw):
        self.db=Fake(results)
        self.scope=RuleScope('offline-workspace',5,9) if task else RuleScope('offline-workspace')
        return InterceptRuleStore(self.db,self.scope,authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),mutation_authorize=lambda *a:True,idle=lambda *a:True,**kw)
    def test_task_list_parent_scope_order_and_bound_projection(self):
        s=self.make([[(5,)],[ROW]]);self.assertEqual(s.list()[0].pattern,'example.test')
        q=[q for _,q in self.db.calls if q];self.assertEqual(q[0].params,(5,9));self.assertIn('task_id=$1',q[1].sql)
        self.assertIn('ORDER BY action,id',q[1].sql);self.assertIn('octet_length',q[1].sql);self.assertIn('CASE',q[1].sql)
    def test_global_order_explicit_scope(self):
        row=list(ROW);row[6]='';s=self.make([[row]],False)
        self.assertEqual(s.split()[1],[]);q=[q for _,q in self.db.calls if q][0];self.assertIn('builtin DESC,id DESC',q.sql);self.assertNotIn('task_intercept_rules',q.sql)
    def test_create_normalizes_before_approval_bound_inputs(self):
        s=self.make([[(5,)],[(7,)],[ROW]]);seen=[];s.mutate=lambda scope,op,id,p:seen.append(p) or True
        result=s.create(RuleProposal('exact_domain','example.test',action='ALLOW'))
        self.assertEqual(result.id,7);self.assertEqual(seen[0].action,'block')
        q=[q for _,q in self.db.calls if q][1];self.assertEqual(q.params,(5,True,'block','exact_domain','example.test',''))
    def test_denial_and_idle_failure_do_not_read_or_write(self):
        for kind in ('mutate','idle'):
            s=self.make([]);setattr(s,kind,lambda *a:False)
            with self.assertRaises(RuleDenied):s.create(RuleProposal('cidr','192.0.2.0/24'))
            self.assertEqual(self.db.calls,[])
    def test_stale_edit_no_mutation(self):
        s=self.make([[(5,)],[ROW]])
        with self.assertRaises(RuleConflict):s.change(7,expected_updated_at='stale',enabled=False)
        self.assertFalse(any(m=='execute' for m,_ in self.db.calls));self.assertEqual(self.db.calls[-1][0],'rollback')
    def test_update_and_toggle_exact_task_and_timestamp(self):
        s=self.make([[(5,)],[ROW],1,[ROW]])
        s.change(7,expected_updated_at=NOW,enabled=False)
        q=next(q for m,q in self.db.calls if m=='execute');self.assertEqual(q.params,(7,NOW,5,False));self.assertIn('id=$1 AND updated_at=$2 AND task_id=$3',q.sql)
    def test_delete_only_exact_row_and_missing_false(self):
        s=self.make([[(5,)],[ROW],1]);self.assertTrue(s.change(7,expected_updated_at=NOW,delete=True))
        s=self.make([[(5,)],[]]);self.assertFalse(s.change(7,expected_updated_at=NOW,delete=True))
    def test_overflow_sentinel_rejects(self):
        row=list(ROW);row[3]=None;s=self.make([[(5,)],[row]])
        with self.assertRaises(RuleUnavailable):s.list()
    def test_parent_missing_no_global_fallback(self):
        s=self.make([[]])
        with self.assertRaises(RuleConflict):s.list()
        self.assertEqual(len([q for _,q in self.db.calls if q]),1)
    def test_rebinding_during_mutation_grant_rejected(self):
        s=self.make([])
        def change(*a):s.executor=object();return True
        s.mutate=change
        with self.assertRaises(RuleDenied):s.create(RuleProposal('exact_ip','192.0.2.1'))
        self.assertEqual(self.db.calls,[])
