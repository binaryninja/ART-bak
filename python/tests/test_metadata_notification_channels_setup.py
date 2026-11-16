import unittest,json
import test_metadata_detail_setup as transport_fixture
import test_notification_channels_read as channel_fixture
from artex.server.intercept_read_setup import bind_metadata_reads
class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport_fixture.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.n=channel_fixture.Tests();self.n.setUp();self.addCleanup(self.n.doCleanups)
        self.t.bundle=bind_metadata_reads(self.t.f.original,notification_channels_service=self.n.service,authorize_global=lambda r,k:k=='notification_channels')
    def test_jwt_then_redacted_source_dto(self):
        self.assertIs(self.t.bundle.notification_channels,self.n.service);self.assertFalse(self.n.queries)
        self.assertEqual(self.t.request('/api/notify/channels',token=False)[0],401);self.assertFalse(self.n.queries)
        status,body=self.t.request('/api/notify/channels');self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['channels'][0]['config']['password'],'__masked__');self.assertNotIn(b'SECRET',body)
    def test_final_configuration_and_read_grants(self):
        with self.assertRaises(RuntimeError):self.t.request('/api/notify/channels',start=lambda:setattr(self.n,'config',False))
        self.n.config=True
        with self.assertRaises(RuntimeError):self.t.request('/api/notify/channels',start=lambda:setattr(self.n,'allowed',False))
    def test_no_mutation_and_conflict_atomic(self):
        self.assertEqual(self.t.request('/api/notify/channels',method='POST')[0],405)
        self.t.f.original.router.add('HEAD','/api/notify/{name}',lambda r,p:None)
        with self.assertRaises(ValueError):bind_metadata_reads(self.t.f.original,notification_channels_service=self.n.service,authorize_global=lambda *a:True)
        self.assertFalse(self.n.queries)
    def test_changed_bounds_rejected_before_sql(self):
        self.n.reader.max_rows+=1
        with self.assertRaises(Exception):bind_metadata_reads(self.t.f.original,notification_channels_service=self.n.service,authorize_global=lambda *a:True)
        self.assertFalse(self.n.queries)
