"""Independent read-only graph view contract regressions."""
from contextlib import nullcontext
import unittest
from artex.agent.graph_context import GraphContext, ContextScope, ContextUnavailable
from artex.db.exploration import ExplorationStore
from test_graph_tools import node
from test_conversation_activity import TransactionFake


class GraphContextReview(unittest.TestCase):
    def make(self,nodes,edges):
        db=TransactionFake([[(3,)],nodes,edges])
        context=GraphContext(ExplorationStore(db,9),ContextScope(3,9,1,'r'),
                             current=lambda scope:True,lifecycle_guard=lambda scope:nullcontext())
        return db,context

    def test_source_recent_done_and_digest_caps_and_overflow_key(self):
        nodes=[node(i,'intent',state='done') for i in range(1,22)]
        nodes += [node(100+i,'digest',{'body':str(i)},'active') for i in range(16)]
        # Digest members are separate facts, leaving completed intents visible.
        nodes += [node(1000+i,'fact') for i in range(16)]
        edges=[(100+i,'covers',1000+i) for i in range(16)]
        db,context=self.make(nodes,edges)
        view=context.snapshot()
        self.assertEqual(len(view['recent_done_intents']),12)
        self.assertEqual(len(view['cold_digests']),15)
        self.assertEqual(view['cold_digests_more'],[100])
        self.assertNotIn('more_digest_ids',view)
        self.assertTrue(all(query.sql.lstrip().startswith('SELECT') for _,query in db.calls))

    def test_edge_overflow_fails_instead_of_guessing_hotness(self):
        db,context=self.make([node(1,'fact'),node(2,'intent',state='open')],
                             [(1,'derived_from',2),(2,'yields',1)])
        context.max_edges=1
        with self.assertRaises(ContextUnavailable):context.snapshot()
        self.assertEqual(db.events[-1],'rollback')
