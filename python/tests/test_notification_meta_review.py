"""Independent bounded source-semantic and disclosure tests; inert SQL only."""
import unittest
from unittest.mock import patch
import test_notification_meta as fixture
import artex.server.notification_meta as service_module
from artex.server.core import Request

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_exact_three_settings_and_source_six_counter_query(self):
        f=self.f;f.settings=[('notify_enabled','0'),('notify_digest_interval_min','not-a-number'),('notify_public_base_url','private-base')]
        f.stats=(0,0,0,0,0,-9223372036854775808)
        value=f.service.handle(f.request).json()
        self.assertFalse(value['enabled']);self.assertEqual(value['digest_interval_min'],'not-a-number')
        self.assertEqual(value['stats']['backlog_age_ms'],-9223372036854775808)
        self.assertEqual(set(value['stats']),{'channels','channels_on','pending','failed','sent_today','backlog_age_ms'})
        self.assertEqual(f.queries[0].params[0],['notify_enabled','notify_public_base_url','notify_digest_interval_min'])
        q=f.queries[1]
        self.assertIn("sent_at >= date_trunc('day', now())",q.sql)
        self.assertIn('WHERE state=$1),0)::bigint',q.sql)
        self.assertNotIn('UTC',q.sql);self.assertNotIn('config',q.sql)
    def test_malformed_counter_types_overflow_and_shape_are_redacted(self):
        f=self.f
        for stats in [(0,0,0,0,True,0),(0,0,0,0,0,2**63),(-1,0,0,0,0,0),(0,0,0,0,0,1.5),(0,0)]:
            with self.subTest(stats=stats):
                f.stats=stats;response=f.service.handle(f.request)
                self.assertEqual(response.status,500)
                self.assertEqual(response.json(),{'error':'Notification overview unavailable'})
    def test_duplicate_unknown_oversize_settings_stop_before_stats(self):
        f=self.f
        for settings in [[('notify_enabled','true'),('notify_enabled','false')],[('password','secret')],[('notify_public_base_url','x'*65537)]]:
            f.settings=settings;f.queries.clear();response=f.service.handle(f.request)
            self.assertEqual(response.status,500);self.assertEqual(len(f.queries),1)
            self.assertNotIn(b'secret',response.body)
    def test_config_revoked_by_serialization_no_private_response(self):
        f=self.f;f.settings=[('notify_public_base_url','private-base')];original=service_module.json_response
        def serialize(status,data):
            result=original(status,data)
            if status==200:f.config=False
            return result
        with patch.object(service_module,'json_response',serialize):response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'private-base',response.body)
    def test_original_executor_replacement_after_first_read_denied(self):
        f=self.f;old=f.reader.executor.fetch_all
        def fetch(q):
            result=old(q);f.reader.executor=object();return result
        f.reader.executor.fetch_all=fetch
        response=f.service.handle(f.request)
        self.assertEqual(response.status,500);self.assertEqual(len(f.queries),1)
    def test_request_mutation_at_guard_exit_denied(self):
        f=self.f;f.exit=lambda:object.__setattr__(f.request,'target','/api/notify/meta?raw=1')
        response=f.service.handle(f.request)
        self.assertEqual(response.status,403);self.assertNotIn(b'stats',response.body)
