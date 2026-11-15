"""Independent constraint admission/atomicity tests, no model or live database."""
import unittest
from dataclasses import FrozenInstanceError
from artex.tools.constraints import ConstraintToolDenied
from artex.agent.runtime import Grant
from test_process import Context as BaseContext
class Context(dict,BaseContext):
    def __init__(self,**changes):
        dict.__init__(self,task_id=3,exploration_id=9,run_id=1,revision="r1")
        BaseContext.__init__(self);self.update(changes)
import test_constraint_tools as fixtures
from test_conversation_activity import TransactionFake

class ConstraintReview(unittest.TestCase):
    make=fixtures.ConstraintToolTests.make
    def test_deny_proposal_also_requires_operator_authorization(self):
        tool=self.make([])
        self.core.proposals=lambda scope,items:Grant(True,True,False)
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'text':'Stop every task'})
        self.assertEqual(self.db.calls,[])
    def test_proposals_are_immutable_and_request_mutation_cannot_rewrite_sql(self):
        tool=self.make([[(3,)],(8,)])
        args={'text':'Exact authorized boundary','type':'allow'}
        def approve(scope,items):
            with self.assertRaises(FrozenInstanceError):items[0].text='different'
            args['text']='different'
            return Grant(True,True,True)
        self.core.proposals=approve
        tool.callback(Context(),args)
        self.assertEqual(self.db.calls[-1][1].params,(9,'allow','Exact authorized boundary','goals'))
    def test_executor_rebind_during_approval_denies_before_sql(self):
        tool=self.make([]);other=TransactionFake([])
        def approve(scope,items):
            self.core.store.executor=other;self.core.graph.executor=other
            return Grant(True,True,True)
        self.core.proposals=approve
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'text':'boundary'})
        self.assertEqual(self.db.calls,[]);self.assertEqual(other.calls,[])
    def test_late_second_insert_error_rolls_back_whole_batch(self):
        tool=self.make([[(3,)],(8,),RuntimeError('fixture insert failure')])
        with self.assertRaises(RuntimeError):tool.callback(Context(),{'constraints':[{'text':'first'},{'text':'second'}]})
        self.assertEqual(self.db.events,['begin','rollback'])
        self.assertEqual(self.db.calls[0][1].params,(3,9))
        self.assertIn('FOR UPDATE',self.db.calls[0][1].sql)
        self.assertFalse(any(q.sql.startswith(('DELETE','UPDATE')) for _,q in self.db.calls))

    def test_foreign_runtime_metadata_cannot_install_current_scope_constraint(self):
        for change in ({'task_id':4},{'exploration_id':10},{'run_id':2},{'revision':'other'},{'run_id':True}):
            tool=self.make([])
            with self.subTest(change=change),self.assertRaises(ConstraintToolDenied):
                tool.callback(Context(**change),{'text':'boundary'})
            self.assertEqual(self.db.calls,[])
