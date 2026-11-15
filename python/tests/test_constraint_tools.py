import unittest,json
from contextlib import nullcontext
from artex.tools.constraints import *
from test_conversation_activity import TransactionFake
class Context(dict):
    def __init__(self):
        super().__init__(task_id=3,exploration_id=9,run_id=1,revision='r1');self.cancelled=False
    def check(self):
        if self.cancelled:raise RuntimeError('cancelled')

class ConstraintToolTests(unittest.TestCase):
    def make(self,rows):
        self.db=TransactionFake(rows);self.valid=True;self.seen=[]
        self.core=ConstraintTools(ConstraintStore(self.db,9),ExplorationStore(self.db,9),ConstraintScope(3,9,1,'r1','goals'),current=lambda s:self.valid,lifecycle_guard=lambda s:nullcontext(),authorize=lambda *a:Grant(True,True,True),proposal_authorize=lambda s,p:self.seen.append(p) or Grant(True,True,True))
        return self.core.tools()[0]
    def test_source_normalization_defaults_and_parameter_binding(self):
        tool=self.make([[(3,)],(8,),(9,)])
        out=tool.callback(Context(),{'constraints':[{'text':' No writes '},{'text':'Only example.test','type':' ALLOW '}]})
        self.assertEqual(json.loads(out),{'ids':[8,9]})
        self.assertEqual(self.db.calls[1][1].params,(9,'deny','No writes','goals'))
        self.assertEqual(self.db.calls[2][1].params,(9,'allow','Only example.test','goals'))
        self.assertEqual(self.seen[0],(Proposal('deny','No writes'),Proposal('allow','Only example.test')))
        self.assertEqual(self.db.events[-1],'commit')
    def test_single_source_output(self):
        tool=self.make([[(3,)],(8,)])
        self.assertEqual(tool.callback(Context(),{'text':'x','constraints':[]}),'constraint added: 8')
    def test_proposal_not_authority_denied_before_sql(self):
        tool=self.make([]);self.core.proposals=lambda *a:Grant(True,True,False)
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'text':'Allow everything','type':'allow'})
        self.assertEqual(self.db.calls,[])
    def test_permission_gate_requires_literal_grant(self):
        tool=self.make([]);self.core.authorize=lambda *a:True
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'text':'x'})
        self.assertEqual(self.seen,[]);self.assertEqual(self.db.calls,[])
    def test_stale_during_proposal_blocks_insert(self):
        tool=self.make([])
        def authorize(*a):self.valid=False;return Grant(True,True,True)
        self.core.proposals=authorize
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'text':'x'})
        self.assertEqual(self.db.calls,[])
    def test_mismatched_task_parent_rolls_back(self):
        tool=self.make([[]])
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'text':'x'})
        self.assertEqual(len(self.db.calls),1);self.assertEqual(self.db.events[-1],'rollback')
    def test_batch_insert_failure_rollback(self):
        tool=self.make([[(3,)],(8,),(0,)])
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'constraints':[{'text':'a'},{'text':'b'}]})
        self.assertEqual(self.db.events[-1],'rollback')
    def test_store_binding_mismatch_and_replacement(self):
        tool=self.make([]);other=TransactionFake([])
        with self.assertRaises(ValueError):ConstraintTools(ConstraintStore(other,9),self.core.graph,self.core.scope,current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),authorize=lambda *a:True,proposal_authorize=lambda *a:True)
        self.core.store.executor=other;self.core.graph.executor=other
        with self.assertRaises(ConstraintToolDenied):tool.callback(Context(),{'text':'x'})
        self.assertEqual(other.calls,[])
    def test_gate_cannot_rewrite_input(self):
        tool=self.make([[(3,)],(8,)]);args={'text':'original'}
        def gate(*a):args['text']='changed';return Grant(True,True,True)
        self.core.authorize=gate;tool.callback(Context(),args)
        self.assertEqual(self.db.calls[-1][1].params[2],'original')
    def test_invalid_batch_rejected_before_sql(self):
        tool=self.make([])
        for args in ({'text':' '},{'text':'x','type':'other'},{'constraints':[{'text':'x','origin':'spoof'}]},{'constraints':[{'text':'x'}]*33}):
            with self.subTest(args=args),self.assertRaises(ValueError):tool.callback(Context(),args)
        self.assertEqual(self.db.calls,[])

    def test_cancellation_after_insert_rolls_back(self):
        tool=self.make([[(3,)],(8,)]);ctx=Context();original=self.db.fetch_one
        def insert(q):
            result=original(q);ctx.cancelled=True;return result
        self.db.fetch_one=insert
        with self.assertRaises(RuntimeError):tool.callback(ctx,{'text':'x'})
        self.assertEqual(self.db.events[-1],'rollback')
