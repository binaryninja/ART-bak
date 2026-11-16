import unittest
import test_intercept_read_setup as transport
import test_missing_skill_read as source
from artex.server.missing_skill_setup import bind_missing_skill_reads
from artex.server.core import Router

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.u=source.Tests();self.u.setUp();self.addCleanup(self.u.doCleanups)
        self.allowed=True;self.grants=[];self.bundle=self.bind()
    def bind(self):return bind_missing_skill_reads(self.t.original,missing_service=self.u.service,authorize_global=lambda r,s,q:self.grants.append(q) or self.allowed)
    def request(self,**kwargs):return self.t.request('/api/skills/missing',application=self.bundle.application,**kwargs)
    def test_inert_jwt_and_same_selection(self):
        self.assertFalse(self.u.db.calls);self.assertFalse(self.grants)
        self.assertEqual(self.t.request('/api/skills/missing',application=self.t.original)[0],404)
        self.assertEqual(self.request(token=False)[0],401);self.assertFalse(self.u.db.calls)
        self.assertEqual(self.request()[0],200);self.assertEqual(len(self.u.db.calls),1)
        self.assertEqual(len({id(q) for q in self.grants+self.u.selections}),1)
    def test_method_bounds_and_unrelated_paths(self):
        for method in ('POST','PUT','DELETE'):self.assertEqual(self.request(method=method)[0],405)
        self.assertEqual(self.request(method='HEAD'),(400,b''))
        self.assertFalse(self.u.db.calls)
        self.assertEqual(self.t.request('/api/health',token=False,application=self.bundle.application)[0],200)
    def test_atomic_conflicts(self):
        for path in ('/api/skills/missing','/api/skills/{name}','/a{mid}/skills/m{tail}'):
            t=transport.Tests();t.setUp();self.addCleanup(t.doCleanups)
            t.original.router.add('HEAD',path,lambda r,p:None);before=tuple(t.original.router._routes)
            with self.assertRaises(ValueError):bind_missing_skill_reads(t.original,missing_service=self.u.service,authorize_global=lambda *a:True)
            self.assertEqual(tuple(t.original.router._routes),before)
    def test_grants_and_postfetch(self):
        self.allowed=False;self.assertEqual(self.request()[0],403);self.assertFalse(self.u.db.calls)
        self.allowed=True;self.u.db.hook=lambda:setattr(self,'allowed',False)
        status,body=self.request();self.assertNotEqual(status,200);self.assertNotIn(b'private-skill',body)
    def test_post_header_all_grants_and_current(self):
        for owner,name in ((self,'allowed'),(self.u,'allowed'),(self.u,'global_allowed'),(self.u,'current')):
            setattr(owner,name,True)
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):
                self.request(start=lambda:setattr(owner,name,False))
            setattr(owner,name,True)
    def test_original_auth_and_reader_replacement(self):
        self.u.db.hook=lambda:setattr(self.t.original.router,'authorize',lambda r:True)
        status,body=self.request();self.assertNotEqual(status,200);self.assertNotIn(b'private-skill',body)
    def test_last_source_current_revokes_outer_or_request(self):
        self.assertEqual(self.request()[0],200)
        final=self.u.current_calls
        for owner,name in ((self,'allowed'),(self.u,'allowed')):
            self.u.current_calls=0
            self.u.current_hook=lambda:setattr(owner,name,False) if self.u.current_calls==final else None
            with self.assertRaisesRegex(RuntimeError,'disclosure revoked'):self.request()
            setattr(owner,name,True)
    def test_modified_source_method_refused_at_bind(self):
        self.u.reader.read=lambda *a,**k:{'missing':[]}
        with self.assertRaises(ValueError):self.bind()
    def test_generic_auth_refused(self):
        self.t.original.router=Router(lambda r:True)
        with self.assertRaises(ValueError):self.bind()

if __name__=='__main__':unittest.main()
