"""Scoped visibility metadata with fake transactions only; no real settings."""
from contextlib import contextmanager,nullcontext
import json,unittest
from artex.db.visibility import *
from artex.server.visibility import VisibilityService
from artex.server.core import Router,Request
class DB:
    def __init__(self):self.calls=[];self.rows=[(2,),(2,)];self.count=1;self.writes=[];self.after=None
    @contextmanager
    def transaction(self):
        old=list(self.writes)
        try:yield self
        except BaseException:self.writes=old;raise
    def fetch_all(self,q):
        self.calls.append(q)
        if 'FROM agents' in q.sql or 'FROM mcp_servers' in q.sql:return [(q.params[0],)]
        return self.rows
    def execute(self,q):
        self.calls.append(q);self.writes.append(q)
        if self.after:self.after()
        return self.count
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.valid=True;self.allowed=True;self.selections=[]
        def grant(scope,action,p):self.selections.append(p);return self.allowed
        self.store=VisibilityStore(self.db,VisibilityScope('catalog','r'),authorize=grant,current=lambda s:self.valid,lifecycle_guard=lambda s:nullcontext(),mutation_authorize=grant)
        self.service=VisibilityService(self.store,authorize=lambda *a:True);self.router=Router(lambda req:req.header('Authorization')=='yes');self.service.register(self.router)
    def call(self,path='/api/visibility/mcp/3',data=None,auth='yes'):
        return self.router.dispatch(Request('GET' if data is None else 'POST',path,{'Authorization':auth},b'' if data is None else json.dumps(data).encode()))
    def test_read_source_string_ids_preserves_duplicates(self):
        self.assertEqual(self.call().json(),{'agents':['2','2']});self.assertEqual(self.db.calls[0].params,('mcp',3))
        self.assertEqual(self.call('/api/visibility/skill/demo').status,200);self.assertEqual(self.db.calls[-1].params,('demo',))
    def test_auth_policy_before_sql(self):
        self.assertEqual(self.call(auth='no').status,401);self.assertEqual(self.db.calls,[])
        self.allowed=False;self.assertEqual(self.call().status,403);self.assertEqual(self.db.calls,[])
    def test_mcp_enable_defaultrow_and_disable_alltool_rows(self):
        self.assertEqual(self.call('/api/visibility/toggle',{'agent_id':'2','kind':'mcp','resource_id':3,'visible':True}).json(),{'ok':True})
        self.assertIn('mcp_tool_name',self.db.writes[-1].sql)
        self.assertEqual(self.call('/api/visibility/toggle',{'agent_id':'2','kind':'mcp','resource_id':3}).status,200)
        q=self.db.writes[-1];self.assertTrue(q.sql.startswith('DELETE'));self.assertNotIn('mcp_tool_name',q.sql);self.assertEqual(q.params,(2,'mcp',3))
    def test_skill_toggle_metadata_only(self):
        for visible in (True,False):self.assertEqual(self.call('/api/visibility/skill/toggle',{'agent_id':'2','skill_name':'demo','visible':visible}).status,200)
        self.assertTrue(self.db.writes[0].sql.startswith('INSERT'));self.assertTrue(self.db.writes[1].sql.startswith('DELETE'))
    def test_invalid_kind_boolean_and_source_wire_ids(self):
        for patch in ({'kind':'tool'},{'visible':1},{'agent_id':2},{'resource_id':'3'}):
            data={'agent_id':'2','kind':'mcp','resource_id':3,'visible':True};data.update(patch)
            self.assertEqual(self.call('/api/visibility/toggle',data).status,400)
        self.assertEqual(self.call('/api/visibility/skill/toggle',{'agent_id':'2','skill_name':'../bad'}).status,400);self.assertEqual(self.db.calls,[])
    def test_read_overflow_no_truncation(self):
        self.db.rows=[(2,)]*1001;self.assertEqual(self.call().status,503)
    def test_executor_rebinding_fails(self):
        def policy(*a):self.store.executor=DB();return True
        self.service.authorize=policy;self.assertEqual(self.call().status,503);self.assertEqual(self.db.calls,[])
    def test_postwrite_stale_is_unknown_and_transaction_rollsback(self):
        self.db.after=lambda:setattr(self,'valid',False)
        r=self.call('/api/visibility/skill/toggle',{'agent_id':'2','skill_name':'demo','visible':True})
        self.assertEqual(r.status,500);self.assertFalse(r.json()['retry']);self.assertEqual(self.db.writes,[])
    def test_missing_mutation_grant_and_badrowcount(self):
        self.store.mutate=None;self.assertEqual(self.call('/api/visibility/skill/toggle',{'agent_id':'2','skill_name':'demo'}).status,503);self.assertEqual(self.db.writes,[])
        self.store.mutate=lambda *a:True;self.db.count=-1
        self.assertEqual(self.call('/api/visibility/skill/toggle',{'agent_id':'2','skill_name':'demo'}).status,500)
