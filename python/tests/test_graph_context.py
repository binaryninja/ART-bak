import unittest
from contextlib import nullcontext
from hashlib import sha256
from artex.agent.graph_context import *
from artex.db.exploration import Node,Edge
from test_graph_tools import node
from test_conversation_activity import TransactionFake
class ColdTests(unittest.TestCase):
    def test_live_ancestors_direct_children_cycles_and_covers_ignored(self):
        nodes=[Node(*node(1,'fact')),Node(*node(2,'intent',state='paused')),Node(*node(3,'fact')),Node(*node(4,'fact')),Node(*node(5,'digest'))]
        graph=ColdGraph(nodes,[Edge(1,'derived_from',2),Edge(2,'yields',3),Edge(3,'derived_from',1),Edge(5,'covers',2),Edge(5,'covers',4)])
        self.assertEqual(graph.hot(),{1,2,3});self.assertEqual(graph.structural_cold(),{4})
    def test_sibling_rescue_does_not_merge_existing_pair(self):
        nodes=[Node(*node(1,'goal'))]+[Node(*node(i,'fact')) for i in range(2,6)]
        graph=ColdGraph(nodes,[Edge(1,'spawns',i) for i in range(2,6)]+[Edge(2,'derived_from',3)])
        self.assertEqual(graph.groups({2,3,4,5}),[ColdBlock((2,3),(1,)),ColdBlock((4,5),(1,))])
    def test_stamp_debounce_signature(self):
        self.assertEqual(stamp_ops({1,2},{1:2,3:1},8),[(2,True,8),(3,False,0)])
        self.assertEqual(eligible_cold({1,2},{1:2,2:3},8),{1})
        self.assertEqual(block_signature(ColdBlock((1,2),(3,)),{1:4,3:7}),sha256(b'm:1:4;m:2:0;a:3:7;').hexdigest())
class ContextTests(unittest.TestCase):
    def make(self,nodes,edges,**kw):
        self.db=TransactionFake([[(3,)],nodes,edges]);self.valid=True
        return GraphContext(ExplorationStore(self.db,9),ContextScope(3,9,1,'r'),current=lambda s:self.valid,lifecycle_guard=lambda s:nullcontext(),**kw)
    def test_actual_store_snapshot_digest_revived_member_visible(self):
        nodes=[node(1,'fact'),node(2,'intent',state='running'),node(3,'fact'),node(4,'digest',{'body':'persisted summary'},'active')]
        view=self.make(nodes,[(1,'derived_from',2),(4,'covers',1),(4,'covers',3)]).snapshot()
        self.assertEqual([r['id'] for r in view['recent_facts']],[1]);self.assertEqual(view['cold_digests'][0]['member_count'],2)
        self.assertEqual(view['hot_ids'],[1,2]);self.assertEqual(self.db.calls[0][1].params,(3,9))
        self.assertTrue(all(q.sql.startswith('SELECT') for _,q in self.db.calls))
    def test_complete_graph_overflow_fails_no_partial_cold_guess(self):
        context=self.make([node(1,'fact'),node(2,'fact')],[],max_nodes=1)
        with self.assertRaises(ContextUnavailable):context.snapshot()
        self.assertEqual(self.db.events[-1],'rollback')
    def test_missing_parent_and_stale_run_reject(self):
        context=self.make([],[]);self.db.results[0]=[]
        with self.assertRaises(ContextUnavailable):context.snapshot()
        context=self.make([],[]);self.valid=False
        with self.assertRaises(ContextUnavailable):context.snapshot()
        self.assertEqual(self.db.calls,[])
    def test_expand_missing_member_and_output_bound(self):
        context=self.make([node(4,'digest',{'body':'x'},'active')],[(4,'covers',8)])
        self.assertEqual(context.expand_digest(4),{'id':4,'state':'active','body':'x','members':[{'id':8,'missing':True}]})
        context=self.make([],[],max_bytes=10)
        with self.assertRaises(ContextUnavailable):context.snapshot()
