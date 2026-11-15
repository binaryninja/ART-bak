"""Scoped node-board SQL plans with an inert executor, not live SQL proof."""
import unittest
from datetime import datetime,timezone
from artex.db.exploration import ExplorationStore,GraphInvalid
from artex.db.nodeboard import NodeBoardStore,NodeFilter


class Executor:
    def __init__(self): self.queries=[];self.rows=[];self.count=(0,)
    def fetch_one(self,q): self.queries.append(q);return self.count
    def fetch_all(self,q): self.queries.append(q);return self.rows
    def execute(self,q): raise AssertionError('writes prohibited')


def row(key=5): return (key,'fact','{"text":"hello"}',0,'confirmed','worker','','','',datetime(2026,11,15,tzinfo=timezone.utc))


class NodeBoardTests(unittest.TestCase):
    def setUp(self):
        self.db=Executor();self.base=ExplorationStore(self.db,7);self.store=NodeBoardStore(self.base)
    def test_page_defaults_and_order(self):
        self.db.rows=[row()];self.db.count=(10,)
        nodes,total=self.store.nodes_page(page=0,size=0)
        self.assertEqual(total,10);self.assertEqual(nodes[0].id,5)
        self.assertEqual(self.db.queries[0].params,(7,))
        self.assertEqual(self.db.queries[1].params,(7,20,0))
        self.assertIn('ORDER BY id DESC LIMIT $2 OFFSET $3',self.db.queries[1].sql)
    def test_filter_bindings_or_group_and_numeric(self):
        self.store.nodes_page(NodeFilter(('fact','intent'),('done',),' #41 ',True),2,900)
        count,page=self.db.queries
        self.assertEqual(count.params,(7,'fact','intent','done','%#41%',41))
        self.assertIn("AND (payload::text ILIKE $5 OR COALESCE(origin,'') ILIKE $5 OR id = $6)",count.sql)
        self.assertIn('kind IN ($2,$3) AND state IN ($4)',count.sql)
        self.assertEqual(page.params,count.params+(200,200))
        self.assertIn('ORDER BY id ASC LIMIT $7 OFFSET $8',page.sql)
    def test_untrusted_filters_bound_not_interpolated(self):
        attack="x') OR true --"
        self.store.nodes_page(NodeFilter((attack,),(),attack))
        self.assertNotIn(attack,self.db.queries[0].sql)
        self.assertEqual(self.db.queries[0].params,(7,attack,'%'+attack+'%'))
    def test_search_wildcards_and_large_number(self):
        self.store.nodes_page(NodeFilter(query='a_%'))
        self.assertEqual(self.db.queries[0].params,(7,'%a_%%'))
        self.db.queries=[];self.store.nodes_page(NodeFilter(query='9223372036854775808'))
        self.assertEqual(len(self.db.queries[0].params),2)
    def test_id_lookups_dedupe_and_scope(self):
        self.db.rows=[row()];nodes=self.store.nodes_by_ids([5,5,8])
        self.assertEqual(nodes[0].id,5)
        self.assertEqual(self.db.queries[0].params,(7,5,8))
        self.assertIn('exploration_id=$1 AND id IN ($2,$3) ORDER BY id',self.db.queries[0].sql)
    def test_edges_touching_no_cross_scope_or_precedence_gap(self):
        self.db.rows=[(5,'yields',8)]
        edges=self.store.edges_touching([5])
        self.assertEqual(edges[0].to_id,8)
        sql=self.db.queries[0].sql
        self.assertIn('AND (e.src_id IN ($2) OR e.dst_id IN ($2))',sql)
        self.assertIn('src.exploration_id=e.exploration_id',sql)
        self.assertIn('dst.exploration_id=e.exploration_id',sql)
    def test_anchors_scoped_and_grouped(self):
        self.db.rows=[(5,20),(5,21),(8,22)]
        self.assertEqual(self.store.node_assets([5,8]),{5:[20,21],8:[22]})
        self.assertIn('JOIN exploration_nodes n ON n.id=a.node_id',self.db.queries[0].sql)
        self.assertIn('n.exploration_id=$1',self.db.queries[0].sql)
    def test_empty_no_reads(self):
        self.assertEqual(self.store.nodes_by_ids([]),[])
        self.assertEqual(self.store.edges_touching([]),[])
        self.assertEqual(self.store.node_assets([]),{})
        self.assertEqual(self.db.queries,[])
    def test_invalid_ids_and_paging_no_queries(self):
        for ids in ([True],[0],[-1],['1'],list(range(10001))):
            with self.assertRaises(ValueError):self.store.nodes_by_ids(ids)
        for page,size in ((True,2),(1,False),(2**63-1,200)):
            with self.assertRaises(ValueError):self.store.nodes_page(page=page,size=size)
        self.assertEqual(self.db.queries,[])
    def test_invalid_filters_before_count(self):
        for f in (NodeFilter(kinds='fact'),NodeFilter(query='a\x00b'),NodeFilter(asc=1),NodeFilter(states=(None,))):
            with self.assertRaises(ValueError):self.store.nodes_page(f)
        self.assertEqual(self.db.queries,[])
    def test_inherited_read_visibility_and_metadata(self):
        inherited=NodeBoardStore(ExplorationStore(self.db,8,read_only=True,source_task_id=2))
        self.db.rows=[row()];nodes,total=inherited.nodes_page()
        self.assertTrue(nodes[0].inherited);self.assertEqual(nodes[0].source_task_id,2)
        self.assertIn("kind <> 'intent' OR state IN ('done','blocked','exhausted','stopped')",self.db.queries[0].sql)
        inherited.nodes_by_ids([5]);self.assertIn("kind <> 'intent'",self.db.queries[-1].sql)
        self.db.rows=[];inherited.edges_touching([5])
        self.assertIn("src.kind <> 'intent'",self.db.queries[-1].sql)
        self.assertIn("dst.kind <> 'intent'",self.db.queries[-1].sql)
        inherited.node_assets([5]);self.assertIn("n.kind <> 'intent'",self.db.queries[-1].sql)
    def test_captured_scope_does_not_follow_changed_store(self):
        self.base.exploration_id=99;self.store.nodes_page()
        self.assertEqual(self.db.queries[0].params,(7,))
    def test_invalid_construction_and_results(self):
        with self.assertRaises(GraphInvalid):NodeBoardStore(object())
        with self.assertRaises(GraphInvalid):NodeBoardStore(ExplorationStore(self.db,7,read_only=True))
        self.db.count=(True,)
        with self.assertRaises(RuntimeError):self.store.nodes_page()
        self.db.rows=[(99,3)]
        with self.assertRaises(RuntimeError):self.store.node_assets([5])

if __name__=='__main__':unittest.main()
