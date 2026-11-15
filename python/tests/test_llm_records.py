import unittest
from artex.db.llm_records import *
from artex.db.postgres import NoRowsError
class Fake:
    def __init__(self,rows=()):self.rows=list(rows);self.calls=[];self.count=1
    def fetch_one(self,q):
        self.calls.append(q);value=self.rows.pop(0)
        if isinstance(value,BaseException):raise value
        return value
    def fetch_all(self,q):return self.fetch_one(q)
    def execute(self,q):self.calls.append(q);return self.count
ROW=(1,None,'model','profile','session','task','worker',1,2,3,4,5,'ok','')
class RecordTests(unittest.TestCase):
    def test_insert_exact_text_and_null_fields(self):
        db=Fake();s=LLMRecordStore(db,task_id='task')
        raw='data: {"dummy":true}\n\n'
        s.insert(LLMRecord(model='m',task_id='task',raw_response=raw,request_body="x';--"))
        q=db.calls[0];self.assertEqual(len(q.params),16);self.assertEqual(q.params[-1],raw);self.assertEqual(q.params[12],"x';--");self.assertIsNone(q.params[1]);self.assertNotIn("x';--",q.sql)
    def test_scope_applies_to_insert_list_detail_picker_and_usage(self):
        db=Fake([(1,),[ROW],NoRowsError(),[]]);s=LLMRecordStore(db,task_id='task')
        rows,total=s.list();self.assertEqual(total,1);self.assertEqual(rows[0].task_id,'task')
        self.assertEqual(db.calls[0].params,('task',));self.assertEqual(db.calls[1].params,('task',50,0))
        self.assertIsNone(s.get(99));self.assertEqual(db.calls[-1].params,(99,'task'))
        self.assertEqual(s.tasks(),[]);self.assertEqual(db.calls[-1].params,('task',))
        for call in (lambda:s.insert(LLMRecord(task_id='other')),lambda:s.list(task='other'),lambda:s.insert_usage(LLMUsage(task_id='other')),lambda:s.token_by_model('other')):
            before=len(db.calls)
            with self.assertRaises(RecordInvalid):call()
            self.assertEqual(len(db.calls),before)
    def test_empty_fixed_scope_not_global(self):
        db=Fake([(0,),[]]);LLMRecordStore(db,task_id='').list()
        self.assertEqual(db.calls[0].params,('',));self.assertIn("COALESCE(task_id,'')",db.calls[0].sql)
    def test_filters_bound_source_wildcards_paging(self):
        db=Fake([(0,),[]]);LLMRecordStore(db).list(model='m',session='s_%',task='t',page=2,size=3)
        self.assertEqual(db.calls[-1].params,('m','%s_%%','t',3,6));self.assertIn('ILIKE $2',db.calls[-1].sql)
        self.assertNotIn('request_body',db.calls[-1].sql)
    def test_missing_detail_vs_driver_failure(self):
        db=Fake([NoRowsError(),RuntimeError('fixture')]);s=LLMRecordStore(db)
        self.assertIsNone(s.get(1))
        with self.assertRaises(RuntimeError):s.get(1)
    def test_full_detail_nullable_bodies_preserved(self):
        db=Fake([ROW+(None,'normalized','raw request','raw response')]);r=LLMRecordStore(db).get(1)
        self.assertEqual(r.request_body,'');self.assertEqual(r.raw_request,'raw request');self.assertNotIn('request_body',r.to_dict())
    def test_usage_exact_eleven_params_unknown_exploration(self):
        db=Fake();s=LLMRecordStore(db);s.insert_usage(LLMUsage(task_id='task',exploration_id=0,model='m',input_tokens=2,status='error'))
        self.assertEqual(db.calls[0].params,('task',None,'','m','',0,2,0,0,0,'error'))
        db.rows=[[('m',1,2,3,4,5)]];self.assertEqual(s.token_by_model('task')[0]['cache_read_tokens'],4)
        self.assertIn('FROM llm_usage',db.calls[-1].sql)
    def test_no_silent_drop_and_validation(self):
        db=Fake();db.count=0;s=LLMRecordStore(db)
        with self.assertRaises(RecordUnavailable):s.insert(LLMRecord())
        with self.assertRaises(RecordUnavailable):s.insert_usage(LLMUsage())
        for call in (lambda:s.list(size=1001),lambda:s.list(page=True),lambda:s.insert(LLMRecord(raw_request='x\0')),lambda:s.insert(LLMRecord(input_tokens=2**32))):
            before=len(db.calls)
            with self.assertRaises(RecordInvalid):call()
            self.assertEqual(len(db.calls),before)
