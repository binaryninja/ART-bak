import unittest,json
import test_metadata_detail_setup as transport_fixture
import test_notification_meta as overview_fixture
from artex.server.intercept_read_setup import bind_metadata_reads
class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport_fixture.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.n=overview_fixture.Tests();self.n.setUp();self.addCleanup(self.n.doCleanups)
        self.t.bundle=bind_metadata_reads(self.t.f.original,notification_meta_service=self.n.service,authorize_global=lambda r,k:k=='notification_meta')
    def test_explicit_jwt_and_source_shape(self):
        self.assertFalse(self.n.queries)
        self.assertIs(self.t.bundle.notification_meta,self.n.service)
        self.assertEqual(self.t.request('/api/notify/meta',token=False)[0],401);self.assertFalse(self.n.queries)
        status,body=self.t.request('/api/notify/meta');self.assertEqual(status,200)
        data=json.loads(body);self.assertEqual(data['defaults']['digest_interval_min'],30)
        self.assertEqual(data['stats']['backlog_age_ms'],-100);self.assertEqual(len(self.n.queries),2)
    def test_final_config_and_operational_denial(self):
        with self.assertRaises(RuntimeError):self.t.request('/api/notify/meta',start=lambda:setattr(self.n,'config',False))
        self.n.config=True
        with self.assertRaises(RuntimeError):self.t.request('/api/notify/meta',start=lambda:setattr(self.n,'allowed',False))
    def test_bound_and_method_replacement_rejected(self):
        self.n.reader.max_bytes+=1
        with self.assertRaises(Exception):bind_metadata_reads(self.t.f.original,notification_meta_service=self.n.service,authorize_global=lambda *a:True)
        self.assertFalse(self.n.queries)
    def test_route_conflict_no_effects_and_no_mutations(self):
        self.assertEqual(self.t.request('/api/notify/meta',method='PUT')[0],405)
        self.t.f.original.router.add('HEAD','/api/notify/{name}',lambda r,p:None)
        with self.assertRaises(ValueError):bind_metadata_reads(self.t.f.original,notification_meta_service=self.n.service,authorize_global=lambda *a:True)
        self.assertFalse(self.n.queries)
