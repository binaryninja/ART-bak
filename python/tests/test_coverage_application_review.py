"""Independent optional-route composition checks; no real data."""
import io,json,unittest
from artex.server.application import build_application,build_persisted_application
from artex.server.auth import sign_jwt
import test_coverage_application as fixtures
from test_application import TestCodec
from test_tasks_store import Fake

class CoverageApplicationReview(unittest.TestCase):
    def request(self,app,path,auth=False):
        status=[];target,_,query=path.partition('?')
        env={'REQUEST_METHOD':'GET','PATH_INFO':target,'QUERY_STRING':query,'wsgi.input':io.BytesIO(b'')}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(body)
    def test_all_optional_routes_absent_without_service(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec())
        for path in ('coverage','coverage-graph','asset-refs?asset_id=7'):
            self.assertEqual(self.request(app,'/api/tasks/5/'+path,True)[0],404)
        self.assertEqual(db.calls,[])
    def test_all_enabled_routes_authenticate_before_resolution(self):
        f=fixtures.Tests();db=Fake([]);service=f.make(db)
        app=build_application(db,b'x'*32,TestCodec(),coverage_service=service)
        for path in ('coverage','coverage-graph','asset-refs?asset_id=7'):
            self.assertEqual(self.request(app,'/api/tasks/5/'+path)[0],401)
        self.assertEqual(f.resolved,[]);self.assertEqual(db.calls,[])
    def test_subclass_registration_override_rejected_before_reads(self):
        from artex.server.task_coverage import TaskCoverageService
        class ForeignService(TaskCoverageService):
            def register(self,router):raise AssertionError('must not run')
        f=fixtures.Tests();db=Fake([]);base=f.make(db)
        service=ForeignService(db,resolve=base.resolve,authorize=base.authorize,current=base.current,lifecycle_guard=base.guard)
        for factory in (build_application,build_persisted_application):
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),coverage_service=service)
        self.assertEqual(db.calls,[])
