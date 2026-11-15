import unittest
from contextlib import nullcontext
from artex.db.task_coverage import TaskCoverageStore,CoverageScope
from artex.db.task_intent_assets import TaskIntentAssetsStore
from artex.server.task_intent_assets import TaskIntentAssetsService
from artex.server.core import Request
from test_tasks_store import Fake
PARENT=[(5,9,True)];SOURCES=[(6,10,None,False)]
ROW=(7,8,'ip','192.0.2.1','anchor','fixture',None,5,False)
class IntentAssetsTests(unittest.TestCase):
    def make(self,rows,authorize=None):
        self.db=Fake(rows);self.allowed=[True];self.request_allowed=[True]
        self.coverage=TaskCoverageStore(self.db,CoverageScope(5,9,'r1'),authorize=authorize or (lambda *a:self.allowed[0]),current=lambda *a:True,lifecycle_guard=lambda *a:nullcontext())
        self.store=TaskIntentAssetsStore(self.coverage);self.service=TaskIntentAssetsService(self.store,authorize=lambda *a:self.request_allowed[0])
    def call(self):return self.service.handle(Request('GET','/api/tasks/5/intent-assets'),{'id':'5'})
    def test_source_sql_shape_and_omitted_null_node(self):
        self.make([PARENT,SOURCES,[ROW,(6,9,'service','fixture:443','system','source',3,6,True)]])
        r=self.call();self.assertEqual(r.status,200);rows=r.json()['assets'];self.assertNotIn('source_node_id',rows[0]);self.assertEqual(rows[1]['source_task_id'],6)
        query=[q for _,q in self.db.calls if q][-1];self.assertEqual(query.params[0],[5,6]);self.assertIn("('done','blocked','exhausted','stopped')",query.sql);self.assertIn('CASE WHEN bytes<=',query.sql)
    def test_source_denied_before_asset_query(self):
        self.make([PARENT,SOURCES],lambda s,t,e:t==5);self.assertEqual(self.call().status,403)
        self.assertEqual(len([q for _,q in self.db.calls if q]),2)
    def test_revoked_after_sql_no_disclosure(self):
        for request in (False,True):
            self.make([PARENT,[],[ROW]]);fetch=self.db.fetch_all
            def revoked(q):
                rows=fetch(q)
                if 'WITH context AS' in q.sql:(self.request_allowed if request else self.allowed)[0]=False
                return rows
            self.db.fetch_all=revoked
            r=self.call();self.assertEqual(r.status,403);self.assertNotIn(b'192.0.2.1',r.body)
    def test_policy_swap_after_sql_rejected(self):
        self.make([PARENT,[],[ROW]]);fetch=self.db.fetch_all
        def swapped(q):
            rows=fetch(q)
            if 'WITH context AS' in q.sql:self.service.authorize=lambda *a:True
            return rows
        self.db.fetch_all=swapped;self.assertEqual(self.call().status,403)
    def test_bounds_and_foreign_provenance_failclosed(self):
        for row in [(7,8,None,None,None,None,None,5,False),(7,8,'ip','private','anchor','',None,99,True)]:
            self.make([PARENT,[],[row]]);self.assertEqual(self.call().status,503)
    def test_no_query_fallback_or_foreign_task(self):
        self.make([]);r=self.service.handle(Request('GET','/api/tasks/6/intent-assets'),{'id':'6'});self.assertEqual(r.status,403);self.assertEqual(self.db.calls,[])
    def test_archived_source_unavailable(self):
        self.make([PARENT,[(6,10,'archive',False)]]);self.assertEqual(self.call().status,503)
