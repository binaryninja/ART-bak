"""Bounded constraint read query contract, no database or user data."""
import unittest
from artex.db.constraints import ConstraintStore,ConstraintReadOverflow


class DB:
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def fetch_all(self,q):self.calls.append(q);return self.rows

class BoundedReadTests(unittest.TestCase):
    def test_one_snapshot_parameter_bound_sql_masks_before_fetch(self):
        db=DB([(1,'deny','rule','human',None,1)])
        result=ConstraintStore(db,9).list_bounded(max_rows=2,max_text_bytes=20,max_origin_bytes=10,max_total_bytes=30)
        self.assertEqual(result[0].text,'rule');self.assertEqual(len(db.calls),1)
        query=db.calls[0];self.assertEqual(query.params,(9,3,2,20,10,30))
        self.assertIn('COUNT(*) OVER ()',query.sql);self.assertIn('SUM(octet_length',query.sql)
        self.assertIn('THEN text ELSE NULL',query.sql);self.assertIn('THEN origin ELSE NULL',query.sql)
        self.assertIn('WHERE exploration_id=$1',query.sql)
    def test_empty_is_complete(self):
        self.assertEqual(ConstraintStore(DB([]),9).list_bounded(),[])
    def test_row_count_and_sentinels_fail_not_partial(self):
        for rows in ([(1,'deny',None,None,None,2)],[(1,'deny','x','',None,3)],
                     [(1,'deny','x','',None,1),(2,'deny','y','',None,1)]):
            with self.assertRaises(ConstraintReadOverflow):ConstraintStore(DB(rows),9).list_bounded(max_rows=1)
    def test_text_origin_and_cumulative_byte_validation(self):
        for rows in ([(1,'deny','éé','',None,1)],[(1,'deny','x','long',None,1)],
                     [(1,'deny','aaa','xx',None,2),(2,'deny','bbb','yy',None,2)]):
            with self.assertRaises(ConstraintReadOverflow):
                ConstraintStore(DB(rows),9).list_bounded(max_text_bytes=3,max_origin_bytes=3,max_total_bytes=8)
    def test_invalid_limits_no_queries(self):
        db=DB([]);store=ConstraintStore(db,9)
        for options in ({'max_rows':True},{'max_rows':0},{'max_text_bytes':-1},{'max_total_bytes':16777217}):
            with self.assertRaises(ValueError):store.list_bounded(**options)
        self.assertEqual(db.calls,[])
