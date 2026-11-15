import unittest,json
from contextlib import nullcontext
from artex.server.task_profiles import *
from artex.server.core import Request
from test_tasks_store import Fake,ROW
from test_profiles import ROW as PROFILE
from test_agents import ROW as AGENT

def reads(chain=True,active=7):
    return [[ROW],[(10,active,active,3,7,0,'ready','',None)] if chain else [(10,None,None,3,None,None,None,None,None)],[],[]]

class TaskProfileTests(unittest.TestCase):
    def make(self,results):
        self.db=Fake(results);self.valid=True
        self.service=TaskProfileService(TaskStore(self.db),ProfileStore(self.db),AgentStore(self.db),resolve=lambda r,id:TaskProfileBinding(id,20,'r1'),authorize=lambda *a:True,profile_authorize=lambda *a:True,current=lambda b:self.valid,lifecycle_guard=lambda b:nullcontext(),configuration_valid=lambda p:True,environment=lambda *a:None,mutation_authorize=lambda *a:True,idle=lambda b:True,coordinate=lambda b,c,write:write())
        return self.service
    def call(self,action='replace',payload=None):
        return self.service.handle(action,Request('PUT' if action=='replace' else 'GET','/api/tasks/10/llm',body=json.dumps(payload or {'llm_profile_ids':[7]}).encode() if action=='replace' else b''),{'id':'10'})
    def test_ordered_chain_real_store_revision_and_source_response(self):
        self.make(reads()+[[PROFILE], [('created',False,False,3)],[],[(7,)],1,1,[(4,)]])
        response=self.call();self.assertEqual(response.status,200,response.body)
        self.assertEqual(response.json(),dict(id='10',llm_profile_ids=[7],active_llm_profile_id=7,llm_failover_state='ready',reopened_intents=0))
        sql=[q for _,q in self.db.calls if q]
        self.assertEqual(next(q.params for q in sql if q.sql.startswith('INSERT INTO task_llm_profiles')),(10,7,0))
        self.assertEqual(self.db.calls[-1][0],'commit')
    def test_stale_revision_before_delete(self):
        self.make(reads()+[[PROFILE],[('created',False,False,4)]])
        self.assertEqual(self.call().status,409)
        self.assertFalse(any(q and q.sql.startswith('DELETE') for _,q in self.db.calls));self.assertEqual(self.db.calls[-1][0],'rollback')
    def test_callback_failure_after_commit_explicit_reconciliation(self):
        self.make(reads()+[[PROFILE],[('created',False,False,3)],[],[(7,)],1,1,[(4,)]])
        def coordinate(b,c,write):write();raise RuntimeError('secret-token-must-not-escape')
        self.service.coordinate=coordinate
        result=self.call();self.assertEqual(result.status,503);self.assertIs(result.json()['committed'],True);self.assertTrue(result.json()['needs_reconciliation']);self.assertNotIn(b'secret-token',result.body)
    def test_missing_coordinator_no_sql(self):
        self.make([]);self.service.coordinate=None;self.assertEqual(self.call().status,503);self.assertEqual(self.db.calls,[])
    def test_invalid_chain_no_sql(self):
        self.make([])
        for body in ({'llm_profile_ids':[7,7]},{'llm_profile_ids':[True]},{'llm_profile_ids':[7],'active_llm_profile_id':8}):
            self.assertEqual(self.call(payload=body).status,400)
        self.assertEqual(self.db.calls,[])
    def test_live_and_quota_blocked_rejected_before_delete(self):
        for status,extra in (('running',[]),('created',[[(55,)]])):
            self.make(reads()+[[PROFILE],[(status,False,False,3)]]+extra)
            self.assertEqual(self.call().status,409)
            self.assertFalse(any(q and q.sql.startswith('DELETE') for _,q in self.db.calls))
    def test_resolution_chain_whitelisted_and_no_environment_fallback(self):
        self.make(reads()+[[],[PROFILE],[],[PROFILE],[],[PROFILE]])
        self.service.environment=lambda *a:self.fail('must not fallback')
        response=self.call('resolution');self.assertEqual(response.status,200,response.body)
        for value in response.json().values():
            self.assertEqual(value['source'],'task_chain');self.assertTrue(value['available']);self.assertEqual(set(value),{'profile_id','name','model','format','source','available'})
        self.assertNotIn(PROFILE[6].encode(),response.body)
    def test_resolution_exhausted_does_not_fallback(self):
        self.make(reads(active=None)+[[],[],[]]);self.service.environment=lambda *a:self.fail('must not fallback')
        response=self.call('resolution');self.assertEqual(response.status,200)
        self.assertTrue(all(not x['available'] and x['source']=='task_chain' for x in response.json().values()))
    def test_agent_binding_precedence(self):
        agent=list(AGENT);agent[-1]=8;profile=list(PROFILE);profile[0]=8
        self.make(reads()+[[tuple(agent)],[tuple(profile)]]*3)
        result=self.call('resolution');self.assertEqual(result.status,200,result.body)
        self.assertTrue(all(x['profile_id']==8 and x['source']=='agent_binding' for x in result.json().values()))
    def test_empty_chain_environment_requires_explicit_confirmed_context(self):
        self.make(reads(False)+[[],[],[],[],[],[]]);self.service.environment=lambda *a:EnvironmentSelection('Explicit','openai','fixture',True)
        result=self.call('resolution');self.assertEqual(result.status,200,result.body)
        self.assertTrue(all(x['source']=='environment' for x in result.json().values()))
    def test_profile_denial_before_private_read(self):
        self.make(reads());self.service.profile_authorize=lambda *a:False
        self.assertEqual(self.call().status,403)
        self.assertFalse(any(q and 'FROM llm_profiles' in q.sql for _,q in self.db.calls))
    def test_empty_chain_clear_preserves_default_semantics(self):
        self.make(reads()+[[('done',False,False,3)],1,[(4,)]])
        result=self.call(payload={'llm_profile_ids':[]});self.assertEqual(result.status,200,result.body)
        self.assertIsNone(result.json()['active_llm_profile_id']);self.assertEqual(result.json()['llm_failover_state'],'default')

    def test_db_chain_order_active_and_quota_reset(self):
        db=Fake([[('done',False,False,4)],[(3,),(7,)],2,1,1,[(5,)]])
        result=TaskStore(db).replace_llm_profiles_idle(10,20,4,[7,3],3)
        self.assertEqual(result,5)
        inserts=[q.params for _,q in db.calls if q and q.sql.startswith('INSERT')]
        self.assertEqual(inserts,[(10,7,0),(10,3,1)])
        update=next(q for _,q in db.calls if q and q.sql.startswith('UPDATE'))
        self.assertEqual(update.params,(10,3,4))
    def test_unconfirmed_write_returns_unknown_not_committed_false(self):
        self.make(reads()+[[PROFILE],[('done',False,False,3)],[(7,)],1,RuntimeError('secret')])
        result=self.call();self.assertEqual(result.status,503);self.assertIsNone(result.json()['committed']);self.assertTrue(result.json()['needs_reconciliation'])
    def test_coordinator_cannot_mask_duplicate_write_attempt(self):
        self.make(reads()+[[PROFILE],[('done',False,False,3)],[(7,)],1,1,[(4,)]])
        def coordinate(b,c,write):
            receipt=write()
            try:write()
            except Exception:pass
            return receipt
        self.service.coordinate=coordinate
        result=self.call();self.assertEqual(result.status,503);self.assertTrue(result.json()['committed'])
