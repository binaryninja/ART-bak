"""Independent metadata bounds and final disclosure checks; inert SQL only."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import test_finding_traffic_read as base
from artex.db.finding_traffic_read import FindingTrafficReader
from artex.server.finding_traffic_read import FindingTrafficReadService
from artex.server.core import Request

class Review(base.Tests):
    # Run only dedicated methods below; inherited owner tests remain separately run.
    def test_review_guard_exit_revokes_http(self):
        @contextmanager
        def guard(s):
            yield
            self.request_allowed=False
        r=FindingTrafficReader(self.db,self.scope,authorize=lambda s:True,current=lambda s:True,lifecycle_guard=guard)
        service=FindingTrafficReadService(r,authorize=lambda *a:self.request_allowed)
        self.assertEqual(service.handle(Request('GET','/api/exploration/findings/11/traffic')).status,403)
    def test_review_last_current_revokes_http_after_render(self):
        from artex.server import finding_traffic_read as module
        rendered=[]
        def current(s):
            if rendered:self.request_allowed=False
            return True
        r=FindingTrafficReader(self.db,self.scope,authorize=lambda s:True,current=current,lifecycle_guard=lambda s:base.nullcontext())
        service=FindingTrafficReadService(r,authorize=lambda *a:self.request_allowed)
        original=module.json_response
        def render(*a):
            response=original(*a);rendered.append(1);return response
        with patch.object(module,'json_response',render):response=service.handle(Request('GET','/api/exploration/findings/11/traffic'))
        self.assertEqual(response.status,403)
    def test_review_selector_mutated_by_grant_refused_before_sql(self):
        request=Request('GET','/api/exploration/findings/11/traffic')
        def grant(req,scope):object.__setattr__(req,'target','/api/exploration/findings/12/traffic');return True
        service=FindingTrafficReadService(self.reader,authorize=grant)
        self.assertEqual(service.handle(request).status,403);self.assertEqual(self.db.calls,[])
    def test_review_response_bound_not_trusted_to_sql_size(self):
        row=list(base.binding());row[4]='x'*2097152;row[19]=1;self.db.rows=[tuple(row)]
        self.assertEqual(self.request().status,503)
    def test_review_order_and_missing_snapshot_refuse(self):
        first=list(base.binding());second=list(base.binding());second[0]=2
        for rows in ([tuple(first),tuple(second)],[tuple(first[:7]+[None]*12+[512])]):
            self.db.rows=rows;self.assertEqual(self.request().status,503)
    def test_review_original_stale_report_sentinel_preserved(self):
        self.db.parent=[(11,2,-1)]
        response=self.request()
        self.assertEqual(response.status,200)
        self.assertEqual(response.json()["report_version"],-1)
    def test_review_rows_bound_refuses_lookahead(self):
        self.db.rows=[base.binding()]*1001
        self.assertEqual(self.request().status,503)

# Avoid duplicate inherited owner tests in test counts.
for name in tuple(vars(base.Tests)):
    if name.startswith('test_'):setattr(Review,name,None)
if __name__=='__main__':unittest.main()
