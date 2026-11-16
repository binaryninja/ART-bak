import unittest
from datetime import datetime,timezone
from contextlib import nullcontext,contextmanager
from unittest.mock import patch
from artex.db.tool_intercept_read import ToolInterceptReader
from artex.server.tool_intercept_read import ToolInterceptReadService
from artex.server.core import Request,json_response
class DB:
    def __init__(self):self.rows=[];self.calls=[];self.hook=None
    def fetch_all(self,q):
        self.calls.append(q)
        if self.hook:self.hook()
        return self.rows
NOW=datetime(2026,1,2,3,4,5,123000,tzinfo=timezone.utc)
def row():return (9,'raw',False,4,'tool_input','regex','[invalid regex','deny','private policy',True,60,'deny',NOW,NOW)
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.reader=ToolInterceptReader(self.db)
        self.service=ToolInterceptReadService(self.reader,authorize=lambda r:self.allowed,current=lambda:True,lifecycle_guard=lambda:nullcontext())
        self.req=Request('GET','/api/intercept/rules')
    def get(self):return self.service.handle(self.req)
    def test_empty_inert_and_exact_source_dto(self):
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.get().json(),{'rules':[]})
        self.db.rows=[row()];d=self.get().json()['rules'][0]
        self.assertEqual(len(d),14);self.assertEqual(d['pattern'],'[invalid regex');self.assertFalse(d['enabled']);self.assertEqual(d['created_at'],'2026-01-02T03:04:05.123Z')
        q=self.db.calls[-1];self.assertEqual(q.params,(1001,1048576));self.assertIn('SUM(',q.sql);self.assertIn('CASE WHEN bytes<=$2',q.sql);self.assertIn('ORDER BY priority DESC,id',q.sql)
    def test_overflow_and_strict_types(self):
        for index,value in ((1,None),(0,True),(2,1),(10,False),(12,object())):
            r=list(row());r[index]=value;self.db.rows=[tuple(r)];self.assertEqual(self.get().status,500)
        self.db.rows=[row()]*1001;self.assertEqual(self.get().status,500)
    def test_denial_before_sql_and_revocation_after_sql(self):
        self.allowed=False;self.assertEqual(self.get().status,403);self.assertEqual(self.db.calls,[])
        self.allowed=True;self.db.rows=[row()];self.db.hook=lambda:setattr(self,'allowed',False);self.assertEqual(self.get().status,403)
    def test_serialization_and_guard_exit_fences(self):
        self.db.rows=[row()]
        def encode(*args):self.allowed=False;return json_response(*args)
        with patch('artex.server.tool_intercept_read.json_response',side_effect=encode):self.assertEqual(self.get().status,403)
        self.allowed=True
        @contextmanager
        def guard():
            yield
            self.allowed=False
        self.service=ToolInterceptReadService(self.reader,authorize=lambda r:self.allowed,current=lambda:True,lifecycle_guard=guard)
        self.assertEqual(self.get().status,403)
    def test_resource_rebind_and_callback_replacement(self):
        for hook in (lambda:setattr(self.reader,'executor',DB()),lambda:setattr(self.service,'authorize',lambda r:self.fail('replacement called'))):
            self.setUp();self.db.hook=hook;self.assertEqual(self.get().status,500)
    def test_mutation_query_and_db_error_rejected(self):
        for req in (Request('POST',self.req.target),Request('GET',self.req.target+'?task=1')):
            self.assertEqual(self.service.handle(req).status,400)
        self.assertEqual(self.db.calls,[])
        def bad():raise ValueError('secret raw error')
        self.db.hook=bad;r=self.get();self.assertEqual(r.status,500);self.assertNotIn(b'secret',r.body)
