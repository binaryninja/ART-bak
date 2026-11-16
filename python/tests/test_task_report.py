import unittest
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
from unittest.mock import patch
from artex.db.task_report import TaskReportStore,ReportScope,ASSET_TYPES,ReportDenied
from artex.server.task_report import TaskReportService
from artex.server.core import Request,Router

class DB:
    def __init__(self):self.calls=[];self.after=lambda q:None;self.payload='{"name":"Local finding","severity":"high","evidence":{"poc":"proof"}}';self.missing=False
    @contextmanager
    def transaction(self):yield self
    def fetch_all(self,q):
        self.calls.append(q)
        if 'FROM tasks' in q.sql:result=[] if self.missing else [('Task','Goal')]
        elif 'FROM exploration_nodes' in q.sql:result=[(self.payload,)]
        elif 'FROM unnest' in q.sql:result=[(k,7 if k=='ip' else 0) for k in ASSET_TYPES]
        else:raise AssertionError(q.sql)
        self.after(q);return result
class Tests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.task=True;self.assets=True;self.http=True
        self.scope=ReportScope(5,9,'r')
        self.store=TaskReportStore(self.db,self.scope,current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),authorize=lambda s:self.task,global_assets_authorize=lambda s,k:self.assets)
        self.service=TaskReportService(self.store,authorize=lambda r,s:self.http,clock=lambda:datetime(2026,1,1,tzinfo=timezone.utc))
    def request(self,target='/api/report?task=5'):return self.service.handle(Request('GET',target))
    def test_source_graph_report_and_global_capped_counts(self):
        r=self.request();self.assertEqual(r.status,200);self.assertIn(b'Local finding',r.body);self.assertIn(b'ip 7',r.body)
        self.assertEqual(r.headers['Content-Type'],'text/markdown; charset=utf-8')
        self.assertEqual(self.db.calls[1].params,(9,));self.assertIn("kind='finding'",self.db.calls[1].sql)
        self.assertIn('LIMIT 1000',self.db.calls[1].sql);self.assertIn('LIMIT 100000',self.db.calls[2].sql)
    def test_missing_or_foreign_selector_no_fallback(self):
        for target in ('/api/report','/api/report?task=6','/api/report?task=05','/api/report?task=5&task=5','/api/report?task=5&q=x'):
            self.assertEqual(self.request(target).status,400)
        self.assertEqual(self.db.calls,[])
        self.db.missing=True;self.assertEqual(self.request().status,404)
    def test_independent_grants_before_sql(self):
        for field in ('task','assets','http'):
            setattr(self,field,False);self.assertEqual(self.request().status,403);setattr(self,field,True)
        self.assertEqual(self.db.calls,[])
    def test_global_callback_cannot_revoke_task_grant(self):
        def assets(s,k):self.task=False;return True
        self.store=TaskReportStore(self.db,self.scope,current=lambda s:True,lifecycle_guard=lambda s:nullcontext(),authorize=lambda s:self.task,global_assets_authorize=assets)
        with self.assertRaises(ReportDenied):self.store.read()
        self.assertEqual(self.db.calls,[])
    def test_fetch_revocation_and_callback_replacement(self):
        self.db.after=lambda q:setattr(self,'assets',False)
        self.assertEqual(self.request().status,403)
        self.assets=True;self.db.after=lambda q:setattr(self.store,'authorize',lambda s:True)
        self.assertEqual(self.request().status,403)
    def test_guard_exit_revocation_direct_store(self):
        @contextmanager
        def guard(s):
            yield
            self.task=False
        self.store=TaskReportStore(self.db,self.scope,current=lambda s:True,lifecycle_guard=guard,authorize=lambda s:self.task,global_assets_authorize=lambda s,k:True)
        with self.assertRaises(ReportDenied):self.store.read()
    def test_sql_sentinel_and_response_overflow(self):
        self.db.payload=None;self.assertEqual(self.request().status,503)
        self.db.payload='{}'
        with patch('artex.server.task_report.markdown',return_value='x'*2097153):self.assertEqual(self.request().status,503)
    def test_final_render_revocation(self):
        def render(*args):self.assets=False;return 'secret'
        with patch('artex.server.task_report.markdown',side_effect=render):r=self.request()
        self.assertEqual(r.status,403);self.assertNotIn(b'secret',r.body)
    def test_explicit_route_requires_parent_auth(self):
        router=Router(lambda r:False);self.service.register(router)
        self.assertEqual(router.dispatch(Request('GET','/api/report?task=5')).status,401)
        self.assertEqual(self.db.calls,[])
