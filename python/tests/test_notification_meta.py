import unittest
from contextlib import contextmanager
from unittest.mock import patch
from artex.db.notification_meta import NotificationMetaReader
from artex.server.notification_meta import NotificationMetaReadService
from artex.server.core import Request
import artex.server.notification_meta as module
class Tests(unittest.TestCase):
    def setUp(self):
        self.settings=[];self.stats=(3,2,5,1,7,-100);self.queries=[];self.allowed=True;self.config=True;self.exit=None
        class DB:
            def fetch_all(db,q):
                self.queries.append(q)
                return self.settings if 'FROM settings' in q.sql else [self.stats]
        @contextmanager
        def guard():
            yield
            if self.exit:self.exit()
        self.reader=NotificationMetaReader(DB())
        self.service=NotificationMetaReadService(self.reader,authorize=lambda r:self.allowed,config_authorize=lambda r:self.config,current=lambda:True,lifecycle_guard=guard)
        self.request=Request('GET','/api/notify/meta')
    def test_source_defaults_order_and_stats(self):
        self.assertFalse(self.queries)
        response=self.service.handle(self.request);self.assertEqual(response.status,200);data=response.json()
        self.assertTrue(data['enabled']);self.assertEqual(data['digest_interval_min'],'');self.assertEqual(data['defaults'],{'digest_interval_min':30})
        self.assertEqual([k['kind'] for k in data['kinds']],['dingtalk','email','feishu','telegram','webhook','wecom'])
        self.assertEqual(data['stats']['backlog_age_ms'],-100)
        self.assertEqual(self.queries[1].params,('pending','sending','failed','sent'))
        self.assertIn("date_trunc('day', now())",self.queries[1].sql)
        self.assertNotIn('AT TIME ZONE',self.queries[1].sql)
    def test_raw_digest_and_exact_boolean(self):
        for enabled,expected in [('true',True),('1',True),('',False),('TRUE',False),(' true ',False)]:
            self.settings=[('notify_enabled',enabled),('notify_digest_interval_min',' 007 '),('notify_public_base_url','https://internal.invalid')]
            data=self.service.handle(self.request).json();self.assertIs(data['enabled'],expected);self.assertEqual(data['digest_interval_min'],' 007 ')
    def test_grants_before_storage_and_after_guard(self):
        self.config=False;self.assertEqual(self.service.handle(self.request).status,403);self.assertFalse(self.queries)
        self.config=True;self.exit=lambda:setattr(self,'config',False)
        response=self.service.handle(self.request);self.assertEqual(response.status,403);self.assertNotIn(b'backlog_age',response.body)
    def test_serialization_revocation_and_request_mutation(self):
        original=module.json_response
        def serialize(*a):
            result=original(*a);self.allowed=False;return result
        with patch.object(module,'json_response',serialize):self.assertEqual(self.service.handle(self.request).status,403)
    def test_bounds_types_and_redaction(self):
        self.settings=[('notify_public_base_url',None)]
        self.assertEqual(self.service.handle(self.request).status,500);self.assertEqual(len(self.queries),1)
        self.settings=[];self.stats=(True,0,0,0,0,0)
        self.assertEqual(self.service.handle(self.request).status,500)
    def test_settings_not_secret_channel_configs(self):
        self.service.handle(self.request)
        self.assertEqual(self.queries[0].params[0],['notify_enabled','notify_public_base_url','notify_digest_interval_min'])
        self.assertIn('SUM(octet_length(value)) OVER ()',self.queries[0].sql)
        self.assertFalse(any('config' in q.sql.lower() for q in self.queries))
    def test_unknown_query_and_mutations_not_supported(self):
        for request in (Request('POST','/api/notify/meta'),Request('GET','/api/notify/meta?secret=1')):
            self.assertEqual(self.service.handle(request).status,400)
        self.assertFalse(self.queries)
