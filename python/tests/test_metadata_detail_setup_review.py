"""Independent dynamic detail composition; JWT/WSGI and inert row adapters."""
import unittest
from artex.server.intercept_read_setup import bind_metadata_reads
from artex.server.intercept_detail_read import InterceptDetailReadService
from artex.server.auth import AuthService
from artex.server.wsgi import WSGIApplication
from test_application import TestCodec
import test_metadata_detail_setup as fixture

class DetailSetupReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_mixed_literal_parameter_conflict_rejected_atomically(self):
        for method,path in (('GET','/a{rest}/intercept/history/{id}'),('HEAD','/api/inter{suffix}/history/777')):
            f=self.fixture();f.f.original.router.add(method,path,lambda *args:None)
            before=tuple(f.f.original.router._routes)
            with self.assertRaises(ValueError):f.bind()
            self.assertEqual(tuple(f.f.original.router._routes),before)
            self.assertFalse(f.d.queries)
    def test_same_original_id_and_scope_at_all_grants(self):
        f=self.fixture();seen=[];raw=[];scope=f.d.reader.scope
        f.d.rows=[(True,777,*f.d.row[2:])]
        f.d.service=InterceptDetailReadService(f.d.reader,authorize=lambda r,s,id:seen.append((s,id)) or True,
            raw_authorize=lambda r,s,id:raw.append((s,id)) or True,current=lambda s:s is scope,lifecycle_guard=f.d.service.guard)
        f.bundle=f.bind();self.assertEqual(f.request('/api/intercept/history/777')[0],200)
        self.assertTrue(seen);self.assertTrue(raw)
        self.assertTrue(all(s is scope and id is seen[0][1] for s,id in seen+raw))
        self.assertEqual(f.d.queries[0].params[:2],(777,'opaque-task'))
    def test_last_jwt_revokes_record_raw_grant_withholds_body(self):
        f=self.fixture();state={'started':False,'calls':0}
        def clock():
            if state['started']:
                state['calls']+=1
                if state['calls']==2:f.d.raw=False
            return 1000.
        auth=AuthService(object(),TestCodec(),f.f.key,clock=clock)
        f.f.original=WSGIApplication(auth.router());f.bundle=f.bind()
        with self.assertRaises(RuntimeError):f.request(start=lambda:state.update(started=True))
        self.assertEqual(len(f.d.queries),1)
    def test_scope_rebind_in_response_callback_cannot_retarget_snapshot(self):
        from artex.db.intercept_history import InspectionScope
        f=self.fixture()
        with self.assertRaises(RuntimeError):f.request(start=lambda:setattr(f.d.reader,'scope',InspectionScope('workspace','other')))
        self.assertEqual(len(f.d.queries),1)

if __name__=='__main__':unittest.main()
