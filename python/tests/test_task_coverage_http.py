"""Real coverage store/SQL construction with inert rows only."""
import unittest
from contextlib import nullcontext
from artex.db.task_coverage import TaskCoverageStore,CoverageScope
from artex.server.task_coverage import TaskCoverageService
from artex.server.core import Router,Request
from test_tasks_store import Fake
from test_task_coverage import PARENT,SOURCES,asset
class Tests(unittest.TestCase):
    def make(self,rows,**kw):
        self.db=Fake(rows);self.allowed=True;self.valid=True;self.reads=[];self.resolve_calls=[]
        self.guard=lambda s:nullcontext()
        self.store=TaskCoverageStore(self.db,CoverageScope(5,9,'r'),authorize=lambda *a:True,current=lambda s:True,lifecycle_guard=self.guard,connector_authorize=kw.pop('connector',None))
        def resolve(req,id):self.resolve_calls.append(id);return self.store
        def allow(req,s,t,e):self.reads.append((t,e));return self.allowed
        opts=dict(resolve=resolve,authorize=allow,current=lambda store:self.valid,lifecycle_guard=self.guard);opts.update(kw)
        self.service=TaskCoverageService(self.db,**opts);self.router=Router(lambda req:req.header('Authorization')=='yes');self.service.register(self.router)
    def call(self,path='coverage',query='',auth='yes',task='5'):
        return self.router.dispatch(Request('GET','/api/tasks/'+task+'/'+path+query,{'Authorization':auth}))
    def queries(self):return [q for _,q in self.db.calls if q]
    def test_coverage_exact_source_shape(self):
        self.make([PARENT,[],[(1,)],[('ip',2,1)]])
        self.assertEqual(self.call().json(),{'enabled':True,'scope_rows':1,'denominator':2,'tested':1,'pct':.5,'by_type':[{'type':'ip','total':2,'tested':1}]})
        self.assertEqual(self.reads,[(5,9)]*4)
    def test_disabled_source_dto_not_fabricated_enabled_results(self):
        self.make([[(5,9,False)],[]]);r=self.call()
        self.assertEqual(r.status,200);self.assertFalse(r.json()['enabled']);self.assertIsNone(r.json()['pct']);self.assertEqual(len(self.queries()),2)
    def test_graph_source_shape(self):
        self.make([PARENT,[],[asset(3,'ip',ip='192.0.2.1')]])
        r=self.call('coverage-graph');self.assertEqual(r.status,200);self.assertEqual(r.json()['nodes'][0]['key'],'a:3');self.assertEqual(r.json()['edges'],[])
    def test_refs_numeric_ids_and_inherited_provenance_buckets(self):
        self.make([PARENT,SOURCES,[(7,'intent','done',{'text':'source'},6),(8,'finding','confirmed',{'summary':'local'},5)]])
        r=self.call('asset-refs','?asset_id=3');self.assertEqual(r.status,200)
        self.assertEqual(r.json()['intents'],[{'id':7,'kind':'intent','state':'done','summary':'source','source_task_id':'6','inherited':True}]);self.assertEqual(r.json()['facts'],[])
        self.assertEqual(self.reads[:2],[(5,9),(6,10)]);self.assertCountEqual(self.reads,[(5,9),(6,10)]*4)
    def test_auth_precedes_resolver_and_policy_precedes_content(self):
        self.make([]);self.assertEqual(self.call(auth='no').status,401);self.assertEqual(self.resolve_calls,[])
        self.allowed=False;self.assertEqual(self.call().status,403);self.assertEqual(self.queries(),[])
    def test_source_denied_no_asset_queries(self):
        self.make([PARENT,SOURCES],authorize=lambda req,s,t,e:t==5)
        self.assertEqual(self.call().status,403);self.assertEqual(len(self.queries()),2)
    def test_invalid_duplicate_unknown_query_before_resolver(self):
        self.make([])
        for path,query in (('asset-refs',''),('asset-refs','?asset_id=0'),('asset-refs','?asset_id=3&asset_id=4'),('coverage','?asset_id=3'),('coverage-graph','?task=6')):
            self.assertEqual(self.call(path,query).status,400)
        self.assertEqual(self.resolve_calls,[])
    def test_missing_foreign_executor_and_guard_fail_closed(self):
        self.make([],resolve=lambda *a:None);self.assertEqual(self.call().status,404)
        self.make([]);self.store.executor=Fake([]);self.assertEqual(self.call().status,503);self.assertEqual(self.queries(),[])
        self.make([]);self.store.guard=lambda s:nullcontext();self.assertEqual(self.call().status,503)
    def test_request_callback_binding_swap_detected(self):
        self.make([])
        def allow(*a):self.store.executor=Fake([]);return True
        self.service.authorize=allow
        self.assertEqual(self.call().status,503);self.assertEqual(self.queries(),[])
    def test_archive_and_internal_errors_redacted(self):
        self.make([PARENT,[(6,10,'archived',False)]]);self.assertEqual(self.call().status,503)
        self.make([])
        def fail(q):raise RuntimeError('password fixture secret')
        self.db.fetch_all=fail;r=self.call();self.assertEqual(r.status,500);self.assertNotIn(b'password',r.body)
    def test_store_denial_cannot_be_overridden_by_http(self):
        self.make([]);self.store.authorize=lambda *a:False
        self.assertEqual(self.call().status,403);self.assertEqual(self.queries(),[])
