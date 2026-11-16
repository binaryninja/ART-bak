"""Independent optional notification overview wrapper; synthetic data only."""
import unittest
from artex.server.intercept_read_setup import bind_metadata_reads
import test_metadata_notification_meta_setup as fixture

class NotificationMetaSetupReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_mixed_executor_refused_before_storage(self):
        f=self.f
        with self.assertRaises(ValueError):bind_metadata_reads(f.t.f.original,
            notification_meta_service=f.n.service,detail_service=f.t.d.service,authorize_global=lambda *a:True)
        self.assertFalse(f.n.queries);self.assertFalse(f.t.d.queries)
    def test_original_jwt_final_callback_config_revocation_withholds_body(self):
        f=self.f;auth=f.t.f.original.router.authorize.__self__;original=auth.clock
        def clock():
            if f.n.queries:f.n.config=False
            return original()
        auth.clock=clock
        f.t.bundle=bind_metadata_reads(f.t.f.original,notification_meta_service=f.n.service,authorize_global=lambda *a:True)
        status,body=f.t.request('/api/notify/meta')
        self.assertEqual(status,403);self.assertNotIn(b'backlog_age',body)
    def test_start_response_current_or_executor_replacement_aborts(self):
        f=self.f
        with self.assertRaises(RuntimeError):f.t.request('/api/notify/meta',start=lambda:setattr(f.n.service,'current',lambda:True))
        f.n.service.current=f.n.service._pins[4]
        with self.assertRaises(RuntimeError):f.t.request('/api/notify/meta',start=lambda:setattr(f.n.reader,'executor',object()))
    def test_unknown_query_and_head_do_not_read(self):
        f=self.f
        self.assertEqual(f.t.request('/api/notify/meta?include=credentials')[0],400)
        self.assertEqual(f.t.request('/api/notify/meta',method='HEAD'),(400,b''))
        self.assertFalse(f.n.queries)
    def test_only_allowlisted_settings_and_no_channel_secrets(self):
        f=self.f;status,body=f.t.request('/api/notify/meta')
        self.assertEqual(status,200)
        self.assertEqual(len(f.n.queries),2)
        self.assertEqual(f.n.queries[0].params[0],['notify_enabled','notify_public_base_url','notify_digest_interval_min'])
        self.assertFalse(any('INSERT' in q.sql or 'UPDATE' in q.sql or 'DELETE' in q.sql for q in f.n.queries))
        self.assertNotIn('SELECT config',f.n.queries[1].sql)
