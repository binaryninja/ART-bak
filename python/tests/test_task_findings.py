import unittest,json
from datetime import datetime,timezone
from contextlib import contextmanager
import test_task_coverage as context
from artex.db.task_findings import TaskFindingsStore
from artex.server.task_findings import TaskFindingsService
from artex.server.core import Request
NOW=datetime(2026,1,1,tzinfo=timezone.utc)
ROW=(11,json.dumps({'vulnclass':'example','name':'test','severity':'low','summary':'synthetic','evidence':{'by':'fixture'}}),NOW,5,'Task',21,'confirmed','[7]',1,1,1)
ASSET=(7,443,'service','https://example.invalid/','GET','','','','','','')
class Tests(unittest.TestCase):
    def make(self,rows=None,**kw):
        self.f=context.CoverageTests();self.coverage=self.f.make(rows if rows is not None else [context.PARENT,[],[ROW],[ASSET]])
        self.store=TaskFindingsStore(self.coverage,asset_authorize=kw.get('assets',lambda *a:True));self.live=True
        self.service=TaskFindingsService(self.store,authorize=lambda *a:self.live)
    def get(self,target='/api/exploration/findings?task=5'):return self.service.handle(Request('GET',target))
    def test_source_node_id_metadata_asset_dto(self):
        self.make();r=self.get();self.assertEqual(r.status,200);item=json.loads(r.body)[0]
        self.assertEqual((item['id'],item['finding_id'],item['status'],item['traffic_count']),('11','21','confirmed',1))
        self.assertEqual(item['assets'],[{'id':'7','type':'service','label':'GET https://example.invalid/'}]);self.assertNotIn('inherited',item)
        self.assertEqual(item['evidence_version'],0);self.assertEqual(item['ts'],'2026-01-01T00:00:00Z')
    def test_inherited_and_missing_standalone(self):
        row=list(ROW);row[3]=6;row[5]=None;row[6]='pending';row[7]='[]';row[8]=0
        self.make([context.PARENT,context.SOURCES,[tuple(row)]]);r=self.get();self.assertEqual(r.status,200)
        item=json.loads(r.body)[0];self.assertEqual(item['source_task_id'],'6');self.assertTrue(item['inherited']);self.assertNotIn('finding_id',item)
    def test_asset_grant_before_asset_sql(self):
        self.make(assets=lambda *a:False);self.assertEqual(self.get().status,403)
        self.assertFalse(any(q and 'FROM assets WHERE' in q.sql for _,q in self.f.db.calls))
    def test_bounds_and_duplicate_mapping_refuse(self):
        for index,value in ((1,None),(9,201),(10,2)):
            row=list(ROW);row[index]=value;self.make([context.PARENT,[],[tuple(row)]])
            self.assertEqual(self.get().status,503)
    def test_request_revocation_after_transaction_exit(self):
        self.make();original=self.f.db.transaction
        @contextmanager
        def tx():
            with original() as value:yield value
            self.live=False
        self.f.db.transaction=tx
        r=self.get();self.assertEqual(r.status,403);self.assertNotIn(b'synthetic',r.body)
    def test_policy_swap_after_query(self):
        self.make();original=self.f.db.fetch_all
        def fetch(q):
            rows=original(q)
            if 'FROM bounded ORDER BY' in q.sql:self.service.authorize=lambda *a:True
            return rows
        self.f.db.fetch_all=fetch;self.assertEqual(self.get().status,403)
    def test_no_scope_fallback(self):
        self.make()
        for target in ('/api/exploration/findings','/api/exploration/findings?task=6','/api/exploration/findings?task=5&page=1'):
            self.assertEqual(self.get(target).status,400)
        self.assertEqual(self.f.db.calls,[])
