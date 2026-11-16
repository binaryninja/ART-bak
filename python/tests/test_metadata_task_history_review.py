"""Independent optional exact-task composition; no live records or endpoints."""
import unittest
from artex.server.intercept_read_setup import bind_metadata_reads
from artex.server.intercept_task_read import TaskInterceptReadService
from artex.server.auth import AuthService
from artex.server.wsgi import WSGIApplication
from test_application import TestCodec
import test_metadata_task_history_setup as fixture
import test_route_inventory_detail as scanner

class TaskCompositionReview(unittest.TestCase):
    def fixture(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_constructor_no_grants_and_same_selection_instance(self):
        f=self.fixture();seen=[]
        service=TaskInterceptReadService(f.h.store,authorize=lambda r,s,v:seen.append(v) or True)
        f.t.bundle=bind_metadata_reads(f.t.f.original,task_history_service=service,authorize_global=lambda *a:True)
        self.assertFalse(seen);self.assertFalse(f.h.queries)
        self.assertEqual(f.t.request('/api/intercept/task/task-text?page=2&size=1')[0],200)
        self.assertTrue(seen);self.assertTrue(all(v is seen[0] for v in seen))
        self.assertEqual((seen[0].task_key,seen[0].page,seen[0].size),('task-text',2,1))
    def test_selection_mutation_by_grant_denied_before_sql(self):
        f=self.fixture()
        def grant(r,s,v):object.__setattr__(v,'task_key','foreign');return True
        service=TaskInterceptReadService(f.h.store,authorize=grant)
        f.t.bundle=bind_metadata_reads(f.t.f.original,task_history_service=service,authorize_global=lambda *a:True)
        status,body=f.t.request('/api/intercept/task/task-text')
        self.assertNotEqual(status,200);self.assertFalse(f.h.queries)
    def test_last_jwt_revokes_raw_no_body(self):
        f=self.fixture();state={'started':False,'calls':0}
        def clock():
            if state['started']:
                state['calls']+=1
                if state['calls']==2:f.h.raw=False
            return 1000.
        original=WSGIApplication(AuthService(object(),TestCodec(),f.t.f.key,clock=clock).router())
        f.t.bundle=bind_metadata_reads(original,task_history_service=f.h.service,authorize_global=lambda *a:True)
        with self.assertRaises(RuntimeError):f.t.request('/api/intercept/task/task-text',start=lambda:state.update(started=True))
        self.assertEqual(len(f.h.queries),1)
    def test_scanner_exact_task_guard_path_only(self):
        for text in ("if 'task_history' in services:staged.add('GET','/api/intercept/task/{id}',handler)","if 'task_history' in services:staged.add('POST','/api/intercept/task/{taskID}',handler)","if 'task_history' in services:staged.add('GET','/api/intercept/task/{taskID}',handler,public=True)"):
            with self.subTest(text=text),self.assertRaises(scanner.fixture.audit.AuditError):scanner.Tests().scan('        '+text)

if __name__=='__main__':unittest.main()
