import unittest
from contextlib import nullcontext,contextmanager
from unittest.mock import patch
from artex.db.judge_usage_read import JudgeUsageReader
from artex.server.judge_usage_read import JudgeUsageReadService
from artex.server.core import Request,json_response
class DB:
    def __init__(self):self.rows=[(0,0,0,0,0,None,None,None,None)];self.calls=[];self.hook=None
    def fetch_all(self,q):
        self.calls.append(q)
        if self.hook:self.hook()
        return self.rows
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.selections=[];self.reader=JudgeUsageReader(self.db)
        self.service=JudgeUsageReadService(self.reader,authorize=lambda r,d:self.selections.append(d) or self.allowed,current=lambda:True,lifecycle_guard=lambda:nullcontext())
    def get(self,query=''):return self.service.handle(Request('GET','/api/intercept/judge/usage'+('?' +query if query else '')))
    def test_actual_query_empty_ledger_and_inert_ctor(self):
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.get().json(),dict(calls=0,input_tokens=0,output_tokens=0,cache_read_tokens=0,cache_write_tokens=0,daily=[]))
        q=self.db.calls[0];self.assertEqual(q.params,(30,368));self.assertEqual(q.sql.count("worker='judge'"),2);self.assertIn("AT TIME ZONE 'UTC'",q.sql);self.assertIn("now()-($1 * interval '1 day')",q.sql)
        self.assertNotIn('status=',q.sql);self.assertNotIn('tool_input',q.sql)
    def test_all_time_totals_distinct_from_daily_and_units(self):
        self.db.rows=[(100,200,300,40,50,'2026-01-01',2,3,4),(100,200,300,40,50,'2026-01-03',1,2,3)]
        value=self.get('days=2').json();self.assertEqual(value['calls'],100);self.assertEqual(len(value['daily']),2);self.assertNotIn('cache_read_tokens',value['daily'][0]);self.assertEqual(self.db.calls[0].params,(2,368))
    def test_source_default_and_stricter_bound(self):
        for value in ('','0','-1','bad','%20%32','99999999999999999999999'):
            self.assertEqual(self.get('days='+value).status,200);self.assertEqual(self.db.calls[-1].params[0],30)
        for q in ('days=367','days=1&days=2','task=1'):
            self.assertEqual(self.get(q).status,400)
    def test_no_fake_zeros_on_storage_failure_or_overflow(self):
        for rows in ([],[(True,0,0,0,0,None,None,None,None)],[(0,0,0,0,0,'bad',1,2,3)],[(0,0,0,0,0,None,1,None,None)]):
            self.db.rows=rows;self.assertEqual(self.get().status,500)
        self.db.rows=[(0,0,0,0,0,None,None,None,None)]*368;self.assertEqual(self.get().status,500)
    def test_grants_before_and_after_sql(self):
        self.allowed=False;self.assertEqual(self.get().status,403);self.assertEqual(self.db.calls,[])
        self.allowed=True;self.db.hook=lambda:setattr(self,'allowed',False);self.assertEqual(self.get().status,403)
    def test_serialization_guard_exit_and_identity_fences(self):
        def encode(*args):self.allowed=False;return json_response(*args)
        with patch('artex.server.judge_usage_read.json_response',side_effect=encode):self.assertEqual(self.get().status,403)
        self.allowed=True
        @contextmanager
        def guard():
            yield
            self.allowed=False
        self.service=JudgeUsageReadService(self.reader,authorize=lambda r,d:self.allowed,current=lambda:True,lifecycle_guard=guard)
        self.assertEqual(self.get().status,403)
        self.setUp();self.db.hook=lambda:setattr(self.service,'authorize',lambda *a:self.fail('rebound callback'))
        self.assertEqual(self.get().status,500)
