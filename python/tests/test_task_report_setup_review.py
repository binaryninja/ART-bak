"""Independent real JWT/WSGI report disclosure tests, inert scoped reader."""
import io,unittest
from dataclasses import replace
from artex.server.auth import sign_jwt
from artex.server.task_report_setup import build_task_report_application
from artex.server.core import Request
import test_intercept_read_setup as transport
import test_task_report as reports

class TaskReportSetupReview(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.r=reports.Tests();self.r.setUp();self.addCleanup(self.r.doCleanups)
        self.started=False;self.after_auth=0;self.clock_hook=None
        def clock():
            if self.started:
                self.after_auth+=1
                if self.clock_hook:self.clock_hook(self.after_auth)
            return 1000.
        self.t.auth.clock=clock
        self.bundle=build_task_report_application(self.t.original,self.r.service)
    def request(self,start=None,query='task=5'):
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/report','QUERY_STRING':query,
             'CONTENT_LENGTH':'0','wsgi.input':io.BytesIO(),
             'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(self.t.key,now=1000.)}
        statuses=[]
        def headers(status,values):
            statuses.append(status)
            if start:start()
        body=b''.join(self.bundle.application(env,headers))
        return statuses[0],body
    def test_last_jwt_clock_callback_revokes_source_no_body(self):
        def revoke(n):
            if n==2:self.r.assets=False
        self.clock_hook=revoke
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self,'started',True))
    def test_postheaders_exact_scope_type_drift_refused(self):
        with self.assertRaises(RuntimeError):
            self.request(start=lambda:object.__setattr__(self.r.scope,'exploration_id',9.0))
    def test_copied_prepared_receipt_never_accepted(self):
        prepared=self.r.service.prepare(Request('GET','/api/report?task=5'))
        with self.assertRaises(Exception):self.r.service.validate_disclosure(replace(prepared))
    def test_conflict_does_not_modify_original_or_read(self):
        self.t.original.router.add('GET','/api/{report}',lambda *a:None)
        before=tuple(self.t.original.router._routes)
        with self.assertRaises(ValueError):build_task_report_application(self.t.original,self.r.service)
        self.assertEqual(tuple(self.t.original.router._routes),before);self.assertEqual(self.r.db.calls,[])
    def test_fixed_selector_no_fallback_and_original_unmodified(self):
        routes=tuple(self.t.original.router._routes)
        self.assertTrue(self.request(query='task=6')[0].startswith('400'))
        self.assertEqual(self.r.db.calls,[])
        status,body=self.request();self.assertTrue(status.startswith('200'));self.assertIn(b'Local finding',body)
        self.assertEqual(tuple(self.t.original.router._routes),routes)
