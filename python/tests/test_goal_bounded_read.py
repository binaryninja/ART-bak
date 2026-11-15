"""Single-query bounded goal projection; no live database."""
import unittest
from artex.db.exploration import ExplorationStore,GraphViewOverflow,GraphInvalid
from test_task_goals_http import node

class DB:
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def fetch_all(self,q):self.calls.append(q);return self.rows

class GoalReadTests(unittest.TestCase):
    def test_bound_parameters_and_sql_sentinels(self):
        db=DB([(*node(2,'goal',{'text':'x'}),1)])
        result=ExplorationStore(db,9).list_goals_bounded(max_rows=3,max_payload_bytes=100,max_origin_bytes=20,max_total_bytes=200)
        self.assertEqual(result[0].payload,{'text':'x'});self.assertEqual(len(db.calls),1)
        q=db.calls[0];self.assertEqual(q.params,(9,4,3,100,20,200))
        self.assertIn('THEN payload ELSE NULL',q.sql);self.assertIn('COUNT(*) OVER ()',q.sql)
        self.assertIn("exploration_id=$1 AND kind='goal'",q.sql);self.assertIn('SUM(octet_length(payload::text)',q.sql)
    def test_empty_complete_and_inherited_read_not_write(self):
        self.assertEqual(ExplorationStore(DB([]),9).list_goals_bounded(),[])
        row=(*node(2,'goal',{'text':'x'}),1)
        result=ExplorationStore(DB([row]),9,read_only=True,source_task_id=5).list_goals_bounded()
        self.assertTrue(result[0].inherited)
    def test_sentinel_and_incomplete_row_count_rejected(self):
        row=list((*node(2,'goal',{'text':'x'}),1));row[2]=None
        for rows in ([row],[(*node(2,'goal',{'text':'x'}),2)]):
            with self.assertRaises(GraphViewOverflow):ExplorationStore(DB(rows),9).list_goals_bounded()
    def test_payload_origin_and_total_bytes_checked(self):
        rows=[(*node(2,'goal',{'text':'é'*50}),1)]
        with self.assertRaises(GraphViewOverflow):ExplorationStore(DB(rows),9).list_goals_bounded(max_payload_bytes=20)
        rows=[(*node(2,'goal',{'text':'x'},origin='long'),1)]
        with self.assertRaises(GraphViewOverflow):ExplorationStore(DB(rows),9).list_goals_bounded(max_origin_bytes=3)
        rows=[(*node(2,'goal',{'text':'x'}),1)]
        with self.assertRaises(GraphViewOverflow):ExplorationStore(DB(rows),9).list_goals_bounded(max_total_bytes=5)
    def test_invalid_bounds_before_sql(self):
        db=DB([])
        for options in ({'max_rows':True},{'max_rows':10001},{'max_payload_bytes':0}):
            with self.assertRaises(GraphInvalid):ExplorationStore(db,9).list_goals_bounded(**options)
        self.assertEqual(db.calls,[])

    def test_single_goal_scoped_projection_and_sentinel(self):
        db=DB([node(2,'goal',{'text':'x'})])
        result=ExplorationStore(db,9).get_goal_bounded(2,max_payload_bytes=100,max_origin_bytes=20)
        self.assertEqual(result.id,2)
        self.assertEqual(db.calls[0].params,(2,9,100,20))
        self.assertIn("AND kind='goal'",db.calls[0].sql)
        self.assertIn('THEN payload ELSE NULL',db.calls[0].sql)
        row=list(node(2,'goal',{'text':'x'}));row[2]=None
        with self.assertRaises(GraphViewOverflow):ExplorationStore(DB([row]),9).get_goal_bounded(2)
        self.assertIsNone(ExplorationStore(DB([]),9).get_goal_bounded(2))
    def test_single_goal_oversized_payload_no_silent_truncation(self):
        with self.assertRaises(GraphViewOverflow):
            ExplorationStore(DB([node(2,'goal',{'text':'x'*70000})]),9).get_goal_bounded(2)
        db=DB([])
        with self.assertRaises(GraphInvalid):ExplorationStore(db,9).get_goal_bounded(2,max_payload_bytes=True)
        self.assertEqual(db.calls,[])
