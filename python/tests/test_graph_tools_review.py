"""Independent graph authority/atomicity checks with inert transaction adapter."""
import unittest
from artex.tools.graph import GraphToolDenied
from artex.agent.runtime import Grant
from artex.db.exploration import GraphNotFound
import test_graph_tools as fixtures
node=fixtures.node
from test_process import Context

class GraphReview(unittest.TestCase):
    make=fixtures.GraphToolsTests.make

    def test_cancellation_during_asset_approval_prevents_insert(self):
        tools=self.make([[(3,)]])
        context=Context()
        def assets(*args):
            context.cancelled=True
            return Grant(True,True,True)
        # This context's check is deliberately an injected cancellation barrier.
        def check():
            if getattr(context,'cancelled',False):raise RuntimeError('cancelled')
        context.check=check;self.bridge.assets=assets
        with self.assertRaises(RuntimeError):tools['record_fact'].callback(context,{'summary':'fixture'})
        self.assertEqual(self.db.events[-1],'rollback')
        self.assertFalse(any(q.sql.startswith('INSERT') for _,q in self.db.calls))

    def test_zero_row_goal_update_rolls_back_proof_link(self):
        tools=self.make([[(3,)],[node(2,'goal')],[node(4,'fact')],1,0])
        with self.assertRaises(GraphNotFound):tools['prove_goal'].callback(Context(),{'goal_id':2,'evidence_id':4})
        self.assertEqual(self.db.events[-1],'rollback')

    def test_duplicate_edge_without_confirmed_existing_row_never_updates_goal(self):
        tools=self.make([[(3,)],[node(2,'goal')],[node(4,'finding')],0,[]])
        with self.assertRaises(GraphToolDenied):tools['prove_goal'].callback(Context(),{'goal_id':2,'evidence_id':4})
        self.assertEqual(self.db.events[-1],'rollback')
        self.assertFalse(any(q.sql.startswith('UPDATE') for _,q in self.db.calls))

    def test_wrong_parent_scope_cannot_read_local_graph(self):
        tools=self.make([[]])
        with self.assertRaises(GraphToolDenied):tools['list_facts'].callback(Context(),{})
        self.assertEqual(len(self.db.calls),1)
        self.assertEqual(self.db.calls[0][1].params,(3,9))

    def test_generation_change_after_insert_rolls_back_fact(self):
        tools=self.make([[(3,)],(8,)])
        original=self.db.fetch_one
        def changed(query):
            result=original(query)
            if query.sql.startswith('INSERT INTO exploration_nodes'):self.valid=False
            return result
        self.db.fetch_one=changed
        with self.assertRaises(GraphToolDenied):tools['record_fact'].callback(Context(),{'summary':'fixture'})
        self.assertEqual(self.db.events[-1],'rollback')

    def test_caller_mutation_during_authorization_does_not_change_granted_fact(self):
        tools=self.make([[(3,)],(8,)])
        args={'summary':'original'}
        def approve(context,action):
            args['summary']='changed'
            return Grant(True,True,True)
        self.bridge.authorize=approve
        tools['record_fact'].callback(Context(),args)
        self.assertIn('original',self.db.calls[-1][1].params[2])
        self.assertNotIn('changed',self.db.calls[-1][1].params[2])
