"""Independent optional view disclosure boundaries with real JWT/WSGI."""
import unittest
from contextlib import nullcontext
from artex.server.intercept_config_read import InterceptConfigReader,InterceptConfigReadService
import test_intercept_read_setup as fixture

class InterceptReadSetupReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_final_service_grant_revokes_current_no_body(self):
        f=self.f;state={'started':False,'current':True}
        def grant(request):
            if state['started']:state['current']=False
            return True
        f.config=InterceptConfigReadService(InterceptConfigReader(f.db),authorize=grant,
            current=lambda:state['current'],lifecycle_guard=lambda:nullcontext())
        f.bundle=f.bind()
        with self.assertRaises(RuntimeError):
            f.request(start=lambda:state.update(started=True))
    def test_original_auth_change_inside_start_response_withholds_body(self):
        f=self.f
        with self.assertRaises(RuntimeError):
            f.request(start=lambda:setattr(f.original.router,'authorize',lambda r:True))
    def test_head_parameter_conflict_is_atomic_before_callbacks(self):
        f=self.f;f.original.router.add('HEAD','/api/intercept/{name}',lambda r,p:None)
        routes=tuple(f.original.router._routes)
        with self.assertRaises(ValueError):f.bind()
        self.assertEqual(tuple(f.original.router._routes),routes)
        self.assertEqual(f.db.calls,[]);self.assertEqual(f.grants,[])
    def test_last_original_jwt_callback_revokes_data_grant_no_body(self):
        from artex.server.auth import AuthService
        from artex.server.wsgi import WSGIApplication
        from test_application import TestCodec
        f=self.f;state={'started':False,'calls':0}
        def clock():
            if state['started']:
                state['calls']+=1
                if state['calls']==2:f.global_allowed=False
            return 1000.
        f.auth=AuthService(object(),TestCodec(),f.key,clock=clock)
        f.original=WSGIApplication(f.auth.router());f.bundle=f.bind()
        with self.assertRaises(RuntimeError):f.request(start=lambda:state.update(started=True))
