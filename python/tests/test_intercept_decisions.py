import unittest
from contextlib import nullcontext,contextmanager
import test_tool_intercept_config as fixture
from artex.db.intercept_decisions import *
NOW=fixture.NOW
class DecisionStoreTests(unittest.TestCase):
    def make(self,rows=(),conversation=False,**kwargs):
        self.db=fixture.DB(rows);self.scope=DecisionScope('synthetic','rev1',conversation_id=7) if conversation else DecisionScope('synthetic','rev1',5,9)
        return InterceptDecisionStore(self.db,self.scope,read_authorize=kwargs.get('read',lambda *a:True),write_authorize=kwargs.get('write',lambda *a:True),current=kwargs.get('current',lambda *a:True),lifecycle_guard=kwargs.get('guard',lambda *a:nullcontext()))
    def test_pending_task_identity_null_audit_and_source(self):
        s=self.make([[(5,)],[(20,)]])
        self.assertEqual(s.create(DecisionRecord('Bash',reason='[模型]synthetic')),20)
        q=[q for q in self.db.calls if isinstance(q,Query)][-1]
        self.assertEqual(q.params[:3],(None,None,'5'));self.assertEqual(q.params[8:10],('model',None));self.assertIn("WHEN $7='pending' THEN NULL",q.sql)
        self.assertIn('commit',self.db.calls)
    def test_terminal_conversation_rule_and_supplied_audit(self):
        audit='{"correlation":"unknown","input_digest":"synthetic","model_input":{"null":null}}'
        s=self.make([[(7,)],[(3,)],[(21,)]],conversation=True)
        r=DecisionRecord('Write',status='denied',rule_id=3,audit_json=audit)
        self.assertEqual(s.create(r),21);q=[q for q in self.db.calls if isinstance(q,Query)][-1]
        self.assertEqual(q.params[:3],(3,7,None));self.assertEqual(q.params[8:10],('rule',audit));self.assertNotIn('run_id',q.params[-1])
    def test_pending_resolution_exact_cas_and_null_preservation(self):
        s=self.make([[(5,)]])
        self.assertTrue(s.resolve(DecisionResolution(20,'timeout','allow','synthetic deadline')))
        q=[q for q in self.db.calls if isinstance(q,Query)][-1]
        self.assertIn("AND status='pending'",q.sql);self.assertIn('audit IS NULL THEN NULL',q.sql);self.assertIn("IS DISTINCT FROM 'exact'",q.sql);self.assertIn('conversation_id IS NULL',q.sql)
        self.assertEqual(q.params[:3],(20,'5','timeout'))
    def test_already_resolved_false_not_overwrite(self):
        s=self.make([[(5,)]])
        self.db.execute=lambda q:self.db.calls.append(q) or 0
        self.assertFalse(s.resolve(DecisionResolution(20,'denied','deny','human')))
    def test_wrong_parent_denied_before_insert(self):
        s=self.make([[]])
        with self.assertRaises(DecisionDenied):s.create(DecisionRecord('Bash'))
        self.assertFalse(any(isinstance(q,Query) and q.sql.startswith('INSERT') for q in self.db.calls))
    def test_read_bounded_scope_and_legacy_null(self):
        row=(20,None,None,'5','worker','Bash','{}','pending','synthetic',None,NOW,'unknown',None,50)
        s=self.make([[(5,)],[row]])
        r=s.get(20);self.assertEqual(r['tool_input'],{});self.assertIsNone(r['audit'])
        q=[q for q in self.db.calls if isinstance(q,Query)][-1];self.assertIn('CASE WHEN bytes<=$3',q.sql);self.assertIn('octet_length',q.sql)
    def test_overflow_no_partial_read(self):
        row=(20,None,None,None,None,None,None,None,None,None,NOW,None,None,2000000)
        s=self.make([[(5,)],[row]])
        with self.assertRaises(DecisionUnavailable):s.get(20)
    def test_postfetch_request_revocation_no_read_disclosure(self):
        row=(20,None,None,'5','','Bash','{}','pending','private',None,NOW,'unknown',None,50)
        s=self.make([[(5,)],[row]]);allowed=[True]
        self.db.on_query=lambda q:allowed.__setitem__(0,False) if 'WITH bounded' in q.sql else None
        with self.assertRaises(DecisionDenied):s.get(20,request_authorize=lambda *a:allowed[0])
    def test_final_write_request_revocation_and_alias_change(self):
        for change in (False,True):
            s=self.make([[(5,)],[(20,)]])
            record=DecisionRecord('Bash');calls=[]
            def grant(*a):
                calls.append(1)
                if len(calls)==3:
                    if change:object.__setattr__(record,'tool_name','Edit')
                    else:return False
                return True
            with self.assertRaises(DecisionDenied):s.create(record,request_authorize=grant)
            self.assertFalse(any(isinstance(q,Query) and q.sql.startswith('INSERT') for q in self.db.calls))
    def test_unknown_commit_sticky_no_retry(self):
        s=self.make([[(5,)],[(20,)]]);self.db.fail_commit=True
        with self.assertRaises(DecisionUncertain):s.create(DecisionRecord('Bash'))
        n=len(self.db.calls)
        with self.assertRaises(DecisionUncertain):s.create(DecisionRecord('Bash'))
        self.assertEqual(len(self.db.calls),n)
    def test_postguard_revocation_after_write_uncertain(self):
        alive=[True]
        @contextmanager
        def guard(*a):
            yield
            alive[0]=False
        s=self.make([[(5,)],[(20,)]],current=lambda *a:alive[0],guard=guard)
        with self.assertRaises(DecisionUncertain):s.create(DecisionRecord('Bash'))
        self.assertIn('commit',self.db.calls)
    def test_invalid_json_scope_and_actions_never_sql(self):
        s=self.make()
        for record in (DecisionRecord('Bash',input_json='{"x":1,"x":2}'),DecisionRecord('Bash',audit_json='null'),DecisionRecord('Bash',input_json='[NaN]')):
            with self.assertRaises((ValueError,RuntimeError)):s.create(record)
        with self.assertRaises(ValueError):s.resolve(DecisionResolution(1,'allowed','deny',''))
        with self.assertRaises(ValueError):DecisionScope('x','r',5,9,7).validate()
        self.assertFalse(self.db.calls)
