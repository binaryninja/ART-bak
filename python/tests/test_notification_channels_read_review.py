"""Independent bounded/redacted list review; no notification transport."""
import json,unittest
from artex.db.notification_channels_read import redacted_config
import test_notification_channels_read as fixture

class ChannelReadReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_malformed_url_port_never_echoes_arbitrary_port_text(self):
        for value in ('https://host:SECRET-PORT/', 'https://host:99999/', 'https://host:-1/'):
            with self.subTest(url=value):self.assertEqual(redacted_config('telegram',{'base_url':value})['base_url'],'__masked__')
    def test_url_del_control_is_masked(self):
        self.assertEqual(redacted_config('telegram',{'base_url':'https://ho\x7fst/'})['base_url'],'__masked__')
    def test_all_declared_secret_containers_and_unknown_kind_suppressed(self):
        from artex.notify import CHANNELS
        for kind,channel in CHANNELS.items():
            config={k:{'nested':['SECRET-SUFFIX']} for k in channel.secret_keys}
            config['unlisted']='SECRET-SUFFIX'
            result=redacted_config(kind,config)
            self.assertEqual(result,{k:'__masked__' for k in channel.secret_keys})
            self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual(redacted_config('unknown',{'password':'SECRET'}),{})
    def test_alias_collision_filter_refuses_entire_response(self):
        f=self.f;row=list(f.rows[0]);row[7]='{"task_ids":[],"TASK_IDS":[1]}'
        f.rows=[tuple(row)];response=f.service.handle(f.request)
        self.assertEqual(response.status,500);self.assertNotIn(b'mail',response.body)
    def test_guard_exit_operational_revoke_withholds_redacted_metadata(self):
        f=self.f;f.exit=lambda:setattr(f,'allowed',False)
        response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'smtp.invalid',response.body)
    def test_untrusted_stats_shape_or_overflow_never_partial_list(self):
        f=self.f;row=list(f.rows[0]);row[1]=True;f.rows=[tuple(row)]
        self.assertEqual(f.service.handle(f.request).status,500)
