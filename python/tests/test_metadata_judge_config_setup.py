import unittest
import test_metadata_detail_setup as transport_fixture
import test_judge_config_read as judge_fixture
from artex.server.intercept_read_setup import bind_metadata_reads

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport_fixture.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.j=judge_fixture.Tests();self.j.setUp();self.addCleanup(self.j.doCleanups)
        self.t.bundle=bind_metadata_reads(self.t.f.original,judge_config_service=self.j.service,authorize_global=lambda r,k:k=='judge_config')
    def test_optional_inert_jwt_and_raw_grants(self):
        self.assertFalse(self.j.queries)
        self.assertEqual(self.t.request('/api/intercept/judge',token=False)[0],401);self.assertFalse(self.j.queries)
        self.j.raw=False;self.assertEqual(self.t.request('/api/intercept/judge')[0],403);self.assertFalse(self.j.queries)
        self.j.raw=True;self.assertEqual(self.t.request('/api/intercept/judge')[0],200);self.assertEqual(len(self.j.queries),1)
        self.assertEqual(self.t.request('/api/intercept/judge/usage')[0],404)
    def test_final_raw_revoke_no_body(self):
        with self.assertRaises(RuntimeError):self.t.request('/api/intercept/judge',start=lambda:setattr(self.j,'raw',False))
        self.assertEqual(len(self.j.queries),1)
    def test_read_only_and_exact_conflict(self):
        self.assertEqual(self.t.request('/api/intercept/judge',method='PUT')[0],405)
        self.assertEqual(self.t.request('/api/intercept/judge',method='HEAD'),(400,b''))
        self.assertEqual(self.t.request('/api/intercept/judge?x=1')[0],400)
        self.t.f.original.router.add('GET','/api/intercept/{kind}',lambda r,p:None)
        with self.assertRaises(ValueError):bind_metadata_reads(self.t.f.original,judge_config_service=self.j.service,authorize_global=lambda *a:True)
        self.assertFalse(self.j.queries)
