import unittest
from decimal import Decimal
from unittest.mock import Mock
from artex.db.llm_records import LLMRecordStore,RecordInvalid,RecordUnavailable

class Tests(unittest.TestCase):
    def test_bounded_sql_ledger_and_raw_grouping(self):
        db=Mock();db.fetch_all.return_value=[('(unknown)',1,Decimal(11),3,2,1),('(unknown)',2,4,2,0,0)]
        rows=LLMRecordStore(db,task_id='5').token_by_model('5',max_rows=3,max_bytes=1024)
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['input_tokens'],11)
        q=db.fetch_all.call_args.args[0]
        self.assertEqual(q.params,('5',1024,4));self.assertIn('FROM llm_usage',q.sql)
        self.assertIn('GROUP BY model',q.sql);self.assertIn('SUM(octet_length(model)) OVER ()',q.sql)
        self.assertNotIn('FROM activity',q.sql);self.assertNotIn('status=',q.sql)
    def test_bad_bounds_no_sql(self):
        db=Mock();store=LLMRecordStore(db,task_id='5')
        for rows,size in ((True,1024),(0,1024),(10001,1024),(1,None),(None,1024),(1,1023)):
            with self.assertRaises(RecordInvalid):store.token_by_model('5',max_rows=rows,max_bytes=size)
        db.fetch_all.assert_not_called()
    def test_overflow_sentinel_and_invalid_aggregate_refuse(self):
        db=Mock();store=LLMRecordStore(db,task_id='5')
        for rows in ([('m',1,0,0,0,0)]*2,[(None,1,0,0,0,0)],[('m',True,0,0,0,0)],
                     [('m',1,-1,0,0,0)],[('m',1,2**63,0,0,0)],[('m',1,Decimal('1.5'),0,0,0)],
                     [('m',1,Decimal('NaN'),0,0,0)],[('m',1,0)], [('x'*1025,1,0,0,0,0)]):
            db.fetch_all.return_value=rows
            with self.assertRaises(RecordUnavailable):store.token_by_model('5',max_rows=1,max_bytes=1024)
    def test_empty_and_legacy_path_preserved(self):
        db=Mock();db.fetch_all.return_value=[];store=LLMRecordStore(db,task_id='5')
        self.assertEqual(store.token_by_model('5',max_rows=1,max_bytes=1024),[])
        self.assertEqual(store.token_by_model('5'),[])
        self.assertEqual(db.fetch_all.call_args.args[0].params,('5',))
        with self.assertRaises(RecordInvalid):store.token_by_model('6',max_rows=1,max_bytes=1024)
