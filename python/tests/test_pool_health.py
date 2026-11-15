import json,unittest
from contextlib import nullcontext
from artex.health import Registry,State
from artex.db.settings import SettingsStore
from artex.server.pool_health import PoolHealthService
from artex.server.core import Request
from test_task_categories import Fake
PROFILE=(7,'fixture','model','openai',3,True,False)
class PoolTests(unittest.TestCase):
    def make(self,results,registry=None):
        self.db=Fake(results);self.registry=registry or Registry(clock=lambda:100)
        self.service=PoolHealthService(SettingsStore(self.db),self.registry,scope='global',authorize=lambda *a:True,current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext(),mutation_authorize=lambda *a:True,idle=lambda *a:True,coordinate=lambda change,apply:apply(),clock=lambda:100)
        return self.service
    def call(self,op,body=None):return self.service.handle(op,Request('GET','/api/test',body=b'' if body is None else json.dumps(body).encode()))
    def test_status_actual_registry_rfc_seconds_redacted_error(self):
        self.make([[('true',)],[('1',)],[PROFILE]])
        self.registry.restore(7,State(0,1,100.2,'Bearer private',99.9))
        r=self.call('status');self.assertEqual(r.status,200);row=r.json()['chain'][0]
        self.assertEqual(row['last_at'],'1970-01-01T00:01:39Z');self.assertEqual(row['cooldown_secs'],1)
        self.assertEqual(row['state'],'tripped');self.assertNotIn(b'private',r.body)
        q=[q for _,q in self.db.calls if q][-1];self.assertNotIn('api_key',q.sql);self.assertIn('is_default DESC,priority DESC,id ASC',q.sql)
    def test_zero_time_omitted_and_exact_boundary_closed(self):
        self.make([[],[],[PROFILE]]);self.registry.restore(7,State(2,1,100,'',0))
        row=self.call('status').json()['chain'][0];self.assertNotIn('last_at',row);self.assertEqual(row['state'],'degraded')
    def test_missing_retry_policy_zero_not_storage_error(self):
        self.make([[]]);r=self.call('retry');self.assertEqual(r.status,200);self.assertEqual(r.json()['breaker'],{'attempts':0,'interval_ms':0})
        self.make([RuntimeError('secret storage error')]);r=self.call('retry');self.assertEqual(r.status,503);self.assertNotIn(b'secret',r.body)
    def test_policy_clamped_before_immutable_approval(self):
        persisted=json.dumps({k:{'attempts':0,'interval_ms':0} for k in ('connect','empty','stream','breaker','intent')})
        self.make([1,[(persisted,)]]);seen=[];self.service.mutate=lambda req,s,p:seen.append(p) or True
        r=self.call('save',{'stream':{'attempts':99,'interval_ms':99999999}});self.assertEqual(r.status,200)
        self.assertIn(('stream',20,3600000),seen[0].policy)
        q=next(q for m,q in self.db.calls if m=='execute');self.assertEqual(q.params[0],'llm_retry_policy')
    def test_missing_coordinator_no_reset_or_write(self):
        self.make([]);self.registry.restore(7,State(1));self.service.coordinate=None
        self.assertEqual(self.call('reset',{'id':7}).status,503);self.assertEqual(self.registry.get(7).fails,1);self.assertEqual(self.db.calls,[])
    def test_reset_forget_failure_is_uncertain_after_memory_change(self):
        def fail(id):raise RuntimeError('private token')
        self.make([],Registry(forget=fail));self.registry.restore(7,State(1))
        r=self.call('reset',{'id':7});self.assertEqual(r.status,503);self.assertTrue(r.json()['needs_reconciliation']);self.assertEqual(self.registry.get(7).fails,0);self.assertNotIn(b'private',r.body)
    def test_reset_all_unavailable_not_unbounded_snapshot(self):
        self.make([]);self.registry.snapshot=lambda:self.fail('snapshot forbidden')
        self.assertEqual(self.call('reset',{}).status,503)
    def test_retained_apply_cannot_run(self):
        self.make([]);saved=[];self.service.coordinate=lambda change,apply:saved.append(apply) or False
        self.assertEqual(self.call('reset',{'id':7}).status,503)
        with self.assertRaises(Exception):saved[0]()
        self.assertEqual(self.db.calls,[])
    def test_idle_callback_rebind_prevents_effect(self):
        self.make([])
        def idle(*a):self.service.scope='foreign';return True
        self.service.idle=idle;self.assertEqual(self.call('reset',{'id':7}).status,403)
        self.assertEqual(self.db.calls,[])
