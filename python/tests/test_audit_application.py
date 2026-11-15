"""Actual WSGI synthetic task audit only; no user logs or database effects."""
from contextlib import nullcontext
import io,json,unittest
from artex.guard import Guard
from artex.server.audit import AuditService,AuditBinding
from artex.server.application import build_application,build_persisted_application
from artex.server.auth import sign_jwt
from test_tasks_store import Fake
from test_application import TestCodec


class Tests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.audit=Guard();self.audit._record('Read','allow')
        def resolve(selector):self.calls.append(selector);return AuditBinding(4,'r',self.audit)
        self.service=AuditService(resolve=resolve,authorize_selection=lambda *a:True,
            raw_authorize=lambda *a:True,current=lambda b:True,lifecycle_guard=lambda b:nullcontext())
    def request(self,app,auth=True):
        status=[];env={'REQUEST_METHOD':'GET','PATH_INFO':'/api/audit','QUERY_STRING':'task=4','CONTENT_LENGTH':'0','wsgi.input':io.BytesIO()}
        if auth:env['HTTP_AUTHORIZATION']='Bearer '+sign_jwt(b'x'*32)
        body=b''.join(app(env,lambda s,h:status.append(s)))
        return int(status[0].split()[0]),json.loads(body)
    def test_inert_construction_auth_and_actual_guard(self):
        db=Fake([]);app=build_application(db,b'x'*32,TestCodec(),audit_service=self.service)
        self.assertEqual(self.calls,[]);self.assertEqual(db.calls,[])
        self.assertEqual(self.request(app,False)[0],401);self.assertEqual(self.calls,[])
        status,payload=self.request(app);self.assertEqual(status,200)
        self.assertEqual(payload['entries'][0]['tool'],'Read');self.assertEqual(self.calls,['4']);self.assertEqual(db.calls,[])
    def test_absent_default_and_missing_capability_are_distinct(self):
        db=Fake([])
        self.assertEqual(self.request(build_application(db,b'x'*32,TestCodec()))[0],404)
        self.assertEqual(self.request(build_application(db,b'x'*32,TestCodec(),audit_service=AuditService()))[0],503)
    def test_rebound_callbacks_and_subclass_refuse_before_reads(self):
        db=Fake([]);self.service.resolve=lambda s:None
        for factory in (build_application,build_persisted_application):
            with self.assertRaises(ValueError):factory(db,b'x'*32,TestCodec(),audit_service=self.service)
        class Other(AuditService):pass
        with self.assertRaises(ValueError):build_persisted_application(db,b'x'*32,TestCodec(),audit_service=Other())
        self.assertEqual(db.calls,[])
    def test_persisted_forward_no_task_or_guard_inference(self):
        class DB(Fake):
            def fetch_all(self,q):
                if 'FROM tasks WHERE deleted_at IS NULL' in q.sql:self.calls.append(('all',q));return []
                return super().fetch_all(q)
        db=DB([]);app=build_persisted_application(db,b'x'*32,TestCodec(),audit_service=self.service)
        self.assertEqual(len(db.calls),1);self.assertEqual(self.calls,[])
        self.assertEqual(self.request(app)[0],200);self.assertEqual(len(db.calls),1)
