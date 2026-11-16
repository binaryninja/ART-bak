import unittest
from contextlib import nullcontext,contextmanager
from datetime import datetime,timezone
from unittest.mock import patch
from artex.db.skill_usage_read import SkillUsageReader,UsageScope,UsageSelection,valid_name
from artex.server.skill_usage_read import SkillUsageReadService
from artex.server.core import Request,json_response

class DB:
    def __init__(self):self.rows=[];self.calls=[];self.hook=None
    def fetch_all(self,q):
        self.calls.append(q)
        if self.hook:self.hook()
        return self.rows
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.allowed=True;self.global_allowed=True;self.selections=[]
        self.reader=SkillUsageReader(self.db,UsageScope('workspace','revision'),authorize_global=lambda s,q:self.global_allowed,current=lambda s:True,lifecycle_guard=lambda:nullcontext())
        self.service=SkillUsageReadService(self.reader,authorize=lambda r,s,q:self.selections.append(q) or self.allowed)
    def get(self,query='',name='sample'):
        return self.service.handle(Request('GET','/api/skills/'+name+'/usage'+('?' +query if query else '')))
    def test_empty_inert_sql(self):
        self.assertEqual(self.db.calls,[]);self.assertEqual(self.get().json(),{'calls':[]})
        q=self.db.calls[0];self.assertEqual(q.params,('sample',50,65536));self.assertIn('AND found',q.sql);self.assertIn('ORDER BY ts DESC LIMIT $2',q.sql);self.assertIn('octet_length(session_id)',q.sql)
        self.assertTrue(all(s is self.selections[0] for s in self.selections))
    def test_wire(self):
        self.db.rows=[(datetime(2026,1,1,microsecond=123000,tzinfo=timezone.utc),'worker',2,'session',17,False)]
        self.assertEqual(self.get().json(),{'calls':[dict(ts='2026-01-01T00:00:00.123Z',agent_key='worker',task_id=2,session_id='session',args_len=17)]})
    def test_limits(self):
        for raw in ('','0','-2','201','no','999999999999999999999999999','%202'):
            self.assertEqual(self.get('limit='+raw).status,200);self.assertEqual(self.db.calls[-1].params[1],50)
        self.assertEqual(self.get('limit=200').status,200);self.assertEqual(self.db.calls[-1].params[1],200)
        for query in ('limit=1&limit=2','task=1'):
            self.assertEqual(self.get(query).status,400)
    def test_names(self):
        for name in ('a','é-１','技能2'):
            self.assertTrue(valid_name(name));self.assertEqual(self.get(name=name).status,200)
        for name in ('A','1a','a--b','a-','a_b','a'*65,'a/one','a%2fone','a%252fone'):
            self.assertEqual(self.get(name=name).status,400)
    def test_independent_grants_and_revoke(self):
        self.global_allowed=False;self.assertEqual(self.get().status,403);self.assertEqual(self.db.calls,[])
        self.global_allowed=True;self.db.hook=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.get().status,403)
    def test_serialization_and_guard_exit(self):
        def encode(*args):self.global_allowed=False;return json_response(*args)
        with patch('artex.server.skill_usage_read.json_response',side_effect=encode):self.assertEqual(self.get().status,403)
        self.global_allowed=True
        @contextmanager
        def guard():
            yield
            self.global_allowed=False
        self.reader=SkillUsageReader(self.db,UsageScope('workspace','revision'),authorize_global=lambda s,q:self.global_allowed,current=lambda s:True,lifecycle_guard=guard)
        self.service=SkillUsageReadService(self.reader,authorize=lambda *a:True)
        self.assertEqual(self.get().status,403)
    def test_bad_rows_and_overflow(self):
        row=(datetime(2026,1,1,tzinfo=timezone.utc),'worker',1,'session',0,False)
        for rows in ([row]*51,[row[:-1]+(True,)],[row[:2]+(True,)+row[3:]],[row[:3]+('x'*65537,)+row[4:]]):
            self.db.rows=rows;self.assertEqual(self.get().status,500)
    def test_replaced_policy_fails(self):
        self.db.hook=lambda:setattr(self.reader,'authorize_global',lambda *a:True)
        self.assertEqual(self.get().status,500)
    def test_nonreentrant_guard(self):
        active=False
        @contextmanager
        def guard():
            nonlocal active
            if active:raise AssertionError('nested')
            active=True
            try:yield
            finally:active=False
        self.reader=SkillUsageReader(self.db,UsageScope('w','r'),authorize_global=lambda *a:True,current=lambda s:True,lifecycle_guard=guard)
        self.service=SkillUsageReadService(self.reader,authorize=lambda *a:True)
        self.assertEqual(self.get().status,200)
