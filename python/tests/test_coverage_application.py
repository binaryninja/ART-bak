"""Explicit application composition with real coverage service and inert SQL."""
from contextlib import nullcontext
import io,json,unittest
from artex.server.application import build_application,build_persisted_application
from artex.server.task_coverage import TaskCoverageService
from artex.db.task_coverage import TaskCoverageStore,CoverageScope
from artex.server.auth import sign_jwt
from test_application import TestCodec
from test_tasks_store import Fake
class Tests(unittest.TestCase):
    def make(self,db):
        guard=lambda s:nullcontext();self.resolved=[]
        store=TaskCoverageStore(db,CoverageScope(5,9,'r'),authorize=lambda *a:True,current=lambda s:True,lifecycle_guard=guard)
        def resolve(req,id):self.resolved.append(id);return store
        return TaskCoverageService(db,resolve=resolve,authorize=lambda *a:True,current=lambda s:True,lifecycle_guard=guard)
    def request(self,app,authorized=True):
        status=[]
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/tasks/5/coverage','QUERY_STRING':'','wsgi.input':io.BytesIO(b'')}
        if authorized:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:status.append(s)));return int(status[0].split()[0]),json.loads(body)
    def test_optin_no_startup_reads_and_auth_before_resolution(self):
        db=Fake([[ (5,9,False) ],[]]);service=self.make(db)
        app=build_application(db,b'x'*32,TestCodec(),coverage_service=service)
        self.assertEqual(db.calls,[]);self.assertEqual(self.resolved,[])
        self.assertEqual(self.request(app,False)[0],401);self.assertEqual(db.calls,[])
        status,result=self.request(app);self.assertEqual(status,200);self.assertFalse(result['enabled']);self.assertEqual(self.resolved,[5])
    def test_default_route_absent(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec())
        self.assertEqual(self.request(app)[0],404);self.assertEqual(db.calls,[])
    def test_foreign_or_invalid_service_before_any_reads(self):
        db=Fake([]);foreign=self.make(Fake([]))
        for factory in (build_application,build_persisted_application):
            for service in (object(),foreign):
                with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),coverage_service=service)
        self.assertEqual(db.calls,[])
    def test_persisted_forwards_exact_service_without_implicit_coverage(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([[(5,9,False)],[]]);service=self.make(db)
        app=build_persisted_application(db,b'x'*32,TestCodec(),coverage_service=service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.resolved,[])
        self.assertEqual(self.request(app)[0],200)
