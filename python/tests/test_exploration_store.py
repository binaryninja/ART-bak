from contextlib import contextmanager
from datetime import datetime, timezone
import json
import unittest
from artex.db.exploration import (ExplorationStore, create_exploration, GraphInvalid,
                                  GraphNotFound, InheritedWriteError)

NOW = datetime(2026,11,15,tzinfo=timezone.utc)
NODE = (5,'intent',{'summary':'test'},4,'open','planner','','','',NOW)


class Fake:
    def __init__(self, results=()):
        self.results, self.calls = list(results), []
    def call(self, op, query):
        self.calls.append((op,query))
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    def fetch_one(self, query): return self.call('one',query)
    def fetch_all(self, query): return self.call('all',query)
    def execute(self, query): return self.call('execute',query)
    @contextmanager
    def transaction(self):
        self.calls.append(('begin',None))
        try:
            yield self
        except BaseException:
            self.calls.append(('rollback',None))
            raise
        else:
            self.calls.append(('commit',None))


class GraphTests(unittest.TestCase):
    def test_root_create_and_task_lookup(self):
        db = Fake([(10,), (None,'goal'), [], [(3,)]])
        self.assertEqual(create_exploration(db,'description','goal'),10)
        store = ExplorationStore(db,10)
        self.assertEqual(store.root(),('','goal'))
        self.assertEqual(store.task_id(),0)
        self.assertEqual(store.task_id(),3)
        self.assertEqual(db.calls[0][1].params,('description','goal'))
        self.assertIn('deleted_at IS NULL',db.calls[-1][1].sql)

    def test_add_node_atomic_anchors_dedup_payload_clean(self):
        db = Fake([(5,),1,1])
        node_id = ExplorationStore(db,10).add_intent({'summary':'a\x00b','literal':r'\u0000'},4,[7,7,8])
        self.assertEqual(node_id,5)
        self.assertEqual([op for op,_ in db.calls],['begin','one','execute','execute','commit'])
        params=db.calls[1][1].params
        self.assertEqual(params[:2],(10,'intent'))
        self.assertEqual(json.loads(params[2]),{'summary':'ab','literal':r'\u0000'})
        self.assertEqual(params[3:],(4,'open','planner'))
        self.assertEqual(db.calls[2][1].params,(5,7))
        self.assertEqual(db.calls[3][1].params,(5,8))
        db=Fake([(5,),RuntimeError('bad asset')])
        with self.assertRaisesRegex(RuntimeError,'bad asset'):
            ExplorationStore(db,10).add_node('goal',{},0,'open','',[99])
        self.assertEqual(db.calls[-1][0],'rollback')

    def test_schema_validation_before_queries(self):
        db=Fake(); store=ExplorationStore(db,10)
        for kind,state in [('bad','open'),('goal','running'),('fact','open')]:
            with self.assertRaises(GraphInvalid):store.add_node(kind,{},0,state)
        for payload in ({'x':float('nan')},{'x':'\ud800'},[],{1:'bad'}):
            with self.assertRaises(GraphInvalid):store.add_goal(payload)
        self.assertEqual(db.calls,[])

    def test_cross_exploration_node_reads_are_bound(self):
        db=Fake([[],[NODE],[NODE],[NODE]])
        store=ExplorationStore(db,10)
        self.assertIsNone(store.get_node(99))
        self.assertEqual(store.get_node(5).to_dict()['payload'],{'summary':'test'})
        self.assertEqual(store.nodes(0)[0].id,5)
        self.assertEqual(store.list_by_kind('intent')[0].id,5)
        self.assertEqual(db.calls[0][1].params,(99,10))
        self.assertIn('exploration_id=$2',db.calls[0][1].sql)
        self.assertEqual(db.calls[2][1].params,(10,2000))
        self.assertEqual(db.calls[3][1].params,(10,'intent',500))

    def test_edge_and_anchor_isolation_and_idempotency(self):
        db=Fake([1,0,0,[(5,'yields',6)]])
        store=ExplorationStore(db,10)
        self.assertTrue(store.link(5,'yields',6))
        self.assertFalse(store.link(5,'yields',6))
        self.assertFalse(store.anchor(99,7))
        sql=db.calls[0][1].sql
        self.assertIn('src.exploration_id=$1 AND dst.exploration_id=$1',sql)
        self.assertIn('ON CONFLICT',sql)
        self.assertEqual(db.calls[0][1].params,(10,5,'yields',6))
        self.assertIn('exploration_id=$3',db.calls[2][1].sql)
        self.assertEqual(db.calls[2][1].params,(99,7,10))
        self.assertEqual(store.edges()[0].to_dict(),{'From':5,'Rel':'yields','To':6})
        with self.assertRaises(GraphInvalid):store.link(5,'yields',5)
        with self.assertRaises(GraphInvalid):store.link(5,'unsafe',6)

    def test_goal_payload_and_state_updates_scoped(self):
        db=Fake([1,0,1,1])
        store=ExplorationStore(db,10)
        store.update_goal_payload(4,'new text','xss')
        self.assertEqual(json.loads(db.calls[0][1].params[0]),{'text':'new text','vulnclass':'xss'})
        self.assertIn("exploration_id=$3 AND kind='goal'",db.calls[0][1].sql)
        self.assertIn('content_version=content_version+1',db.calls[0][1].sql)
        with self.assertRaises(GraphNotFound):store.update_payload(99,{})
        store.set_node_state(4,'met')
        self.assertEqual(db.calls[2][1].params,('met',4,10,['goal']))
        self.assertTrue(store.set_intent_state(5,'done'))
        self.assertEqual(db.calls[3][1].params,('done',5,10,True))

    def test_compare_set_claim_and_frontier(self):
        db=Fake([0,1,1,[NODE],(True,),(False,),[('goal',1),('intent',3)]])
        store=ExplorationStore(db,10)
        self.assertFalse(store.compare_and_set_intent_state(5,'running','done'))
        self.assertTrue(store.compare_and_set_intent_state(5,'running','paused'))
        self.assertEqual(db.calls[1][1].params,('paused',5,10,'running',False))
        self.assertIn('AND state=$4',db.calls[1][1].sql)
        self.assertTrue(store.claim_intent(5,'worker'))
        self.assertIn("AND state='open'",db.calls[2][1].sql)
        self.assertEqual(store.frontier()[0].id,5)
        self.assertIn('ORDER BY priority DESC, id ASC',db.calls[3][1].sql)
        self.assertTrue(store.has_active_intent())
        self.assertFalse(store.has_open_goal())
        self.assertEqual(store.stats(),{'goal':1,'intent':3})

    def test_pagination_and_origin(self):
        db=Fake([[NODE,NODE],[],[(9,)]])
        store=ExplorationStore(db,10)
        nodes,more=store.list_by_kind_page('intent',before=8,limit=1)
        self.assertEqual(len(nodes),1); self.assertTrue(more)
        self.assertEqual(db.calls[0][1].params,(10,'intent',8,2))
        self.assertEqual(store.origin_fact_id(),0)
        self.assertEqual(store.origin_fact_id(),9)

    def test_direct_sources_read_only_and_never_recursive(self):
        db=Fake([[(7,20,'source','goal','done')],[NODE]])
        source=ExplorationStore(db,10).direct_sources()[0]
        self.assertEqual(source.task_id,7)
        node=source.store.get_node(5)
        self.assertTrue(node.inherited); self.assertEqual(node.source_task_id,7)
        self.assertEqual(source.store.direct_sources(),[])
        actions=[lambda:source.store.add_goal({}), lambda:source.store.anchor(5,7),
                 lambda:source.store.link(5,'yields',6),lambda:source.store.update_payload(5,{}),
                 lambda:source.store.set_node_state(5,'done'),lambda:source.store.claim_intent(5,'w')]
        for action in actions:
            with self.assertRaises(InheritedWriteError):action()
        self.assertEqual(len(db.calls),2)

    def test_deletion_explicitly_unimplemented(self):
        db=Fake()
        with self.assertRaises(NotImplementedError):ExplorationStore(db,10).delete_node(5)
        self.assertEqual(db.calls,[])


if __name__=='__main__':unittest.main()
