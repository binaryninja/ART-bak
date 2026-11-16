"""Independent optional channel metadata transport boundary tests."""
import unittest
from artex.server.intercept_read_setup import bind_metadata_reads
import test_metadata_notification_channels_setup as fixture

class ChannelsSetupReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_different_executor_refused_without_reads(self):
        f=self.f
        with self.assertRaises(ValueError):bind_metadata_reads(f.t.f.original,
            notification_channels_service=f.n.service,detail_service=f.t.d.service,authorize_global=lambda *a:True)
        self.assertFalse(f.n.queries);self.assertFalse(f.t.d.queries)
    def test_final_original_jwt_can_revoke_configuration(self):
        f=self.f;auth=f.t.f.original.router.authorize.__self__;clock=auth.clock
        def checking_clock():
            if f.n.queries:f.n.config=False
            return clock()
        auth.clock=checking_clock
        f.t.bundle=bind_metadata_reads(f.t.f.original,notification_channels_service=f.n.service,authorize_global=lambda *a:True)
        status,body=f.t.request('/api/notify/channels')
        self.assertEqual(status,403);self.assertNotIn(b'smtp.invalid',body)
    def test_start_response_replaced_current_or_executor_withholds_body(self):
        f=self.f;current=f.n.service.current
        with self.assertRaises(RuntimeError):f.t.request('/api/notify/channels',start=lambda:setattr(f.n.service,'current',lambda:True))
        f.n.service.current=current
        with self.assertRaises(RuntimeError):f.t.request('/api/notify/channels',start=lambda:setattr(f.n.reader,'executor',object()))
    def test_unknown_query_head_and_delete_do_not_query(self):
        f=self.f
        self.assertEqual(f.t.request('/api/notify/channels?raw=true')[0],400)
        self.assertEqual(f.t.request('/api/notify/channels',method='HEAD'),(400,b''))
        self.assertEqual(f.t.request('/api/notify/channels',method='DELETE')[0],405)
        self.assertFalse(f.n.queries)
    def test_unknown_config_and_url_risks_remain_redacted_through_transport(self):
        import json
        f=self.f;row=list(f.n.rows[0]);row[3]='telegram';row[5]=json.dumps({'bot_token':'SECRET-token','base_url':'https://host:SECRET-port/','new_config':'SECRET-unknown'})
        f.n.rows=[tuple(row)]
        status,body=f.t.request('/api/notify/channels')
        self.assertEqual(status,200);self.assertNotIn(b'SECRET',body)
        self.assertEqual(json.loads(body)['channels'][0]['config'],{'bot_token':'__masked__','base_url':'__masked__'})
