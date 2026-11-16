"""Independent task report authorization, selector, and bounds review."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from artex.db.task_report import TaskReportStore,ASSET_TYPES
from artex.server.task_report import TaskReportService
from artex.server.core import Request
import test_task_report as fixture

class TaskReportReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_final_store_callback_cannot_revoke_http_grant(self):
        f=self.f;rendered=[]
        def current(scope):
            if rendered:f.http=False
            return True
        f.store=TaskReportStore(f.db,f.scope,current=current,lifecycle_guard=lambda s:nullcontext(),authorize=lambda s:f.task,global_assets_authorize=lambda s,k:f.assets)
        f.service=TaskReportService(f.store,authorize=lambda r,s:f.http,clock=f.service.clock)
        def render(*args):rendered.append(True);return 'PRIVATE REPORT'
        with patch('artex.server.task_report.markdown',side_effect=render):response=f.request()
        self.assertEqual(response.status,403);self.assertNotIn(b'PRIVATE',response.body)
    def test_request_selector_mutation_during_authorization_refused(self):
        f=self.f;request=Request('GET','/api/report?task=5')
        def authorize(req,scope):
            object.__setattr__(req,'target','/api/report?task=6');return True
        f.service=TaskReportService(f.store,authorize=authorize,clock=f.service.clock)
        response=f.service.handle(request)
        self.assertNotEqual(response.status,200);self.assertFalse(f.db.calls)
    def test_finding_aggregate_limit_is_independently_checked(self):
        f=self.f;read=f.db.fetch_all
        def oversized(q):
            if 'FROM exploration_nodes' in q.sql:return [('x'*65536,)]*17
            return read(q)
        f.db.fetch_all=oversized
        self.assertEqual(f.request().status,503)
    def test_global_counts_bool_duplicate_and_over_cap_refused(self):
        f=self.f;read=f.db.fetch_all
        for values in ([('ip',1)]*6,[(k,True if k=='ip' else 0) for k in ASSET_TYPES],[(k,100001 if k=='ip' else 0) for k in ASSET_TYPES]):
            def rows(q):return values if 'FROM unnest' in q.sql else read(q)
            f.db.fetch_all=rows
            self.assertEqual(f.request().status,503)
    def test_local_report_queries_do_not_read_global_findings_or_facts(self):
        f=self.f;self.assertEqual(f.request().status,200)
        sql='\n'.join(q.sql for q in f.db.calls)
        self.assertNotIn('FROM findings',sql);self.assertNotIn("kind='fact'",sql)
        self.assertEqual(f.db.calls[0].params,(5,9));self.assertEqual(f.db.calls[1].params,(9,))
        self.assertEqual(f.db.calls[2].params,(list(ASSET_TYPES),))
