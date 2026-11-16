import json,unittest
import test_intercept_read_setup as transport
import test_skill_usage_read as usage
from artex.server.skill_usage_setup import bind_skill_usage_reads
class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.u=usage.Tests();self.u.setUp();self.addCleanup(self.u.doCleanups)
        self.allowed=True;self.grants=[];self.bundle=self.bind()
    def bind(self):return bind_skill_usage_reads(self.t.original,usage_service=self.u.service,authorize_global=lambda r,s,q:self.grants.append(q) or self.allowed)
    def request(self,**kwargs):return self.t.request('/api/skills/sample/usage',application=self.bundle.application,**kwargs)
    def test_inert_explicit_jwt_same_selection(self):
        self.assertFalse(self.u.db.calls);self.assertFalse(self.grants)
        self.assertEqual(self.t.request('/api/skills/sample/usage',application=self.t.original)[0],404)
        self.assertEqual(self.request(token=False)[0],401);self.assertFalse(self.u.db.calls)
        self.assertEqual(self.request()[0],200);self.assertEqual(len(self.u.db.calls),1)
        self.assertEqual(len({id(q) for q in self.grants+self.u.selections}),1)
    def test_conflicts_atomic_and_no_mutation(self):
        self.assertEqual(self.request(method='POST')[0],405)
        for path in ('/api/skills/{x}/usage','/api/s{mid}ls/example/usage','/a{mid}/skills/{x}/u{tail}'):
            t=transport.Tests();t.setUp();self.addCleanup(t.doCleanups)
            t.original.router.add('HEAD',path,lambda r,p:None);before=tuple(t.original.router._routes)
            with self.assertRaises(ValueError):bind_skill_usage_reads(t.original,usage_service=self.u.service,authorize_global=lambda *a:True)
            self.assertEqual(tuple(t.original.router._routes),before)
    def test_postfetch_and_headers(self):
        self.u.db.hook=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.request()[0],403)
        self.allowed=True;self.u.db.hook=None
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self,'allowed',False))
    def test_request_and_source_grants_after_headers(self):
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self.u,'global_allowed',False))
        self.u.global_allowed=True
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self.u,'allowed',False))
    def test_resource_replacement(self):
        self.u.db.hook=lambda:setattr(self.t.original.router,'authorize',lambda r:True)
        self.assertNotEqual(self.request()[0],200)
    def test_source_invalid_name_status_preserved(self):
        self.assertEqual(self.t.request('/api/skills/UPPER/usage',application=self.bundle.application)[0],400)
        self.assertFalse(self.u.db.calls)
