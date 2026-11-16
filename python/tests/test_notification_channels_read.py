import unittest,json
from datetime import datetime,timezone
from contextlib import contextmanager
from unittest.mock import patch
from artex.db.notification_channels_read import NotificationChannelsReader,redacted_config
from artex.server.notification_channels_read import NotificationChannelsReadService
from artex.server.core import Request
import artex.server.notification_channels_read as module
class Tests(unittest.TestCase):
    def setUp(self):
        self.queries=[];self.allowed=True;self.config=True;self.exit=None
        now=datetime(2026,1,1,tzinfo=timezone.utc)
        self.rows=[(True,1,'mail','email',True,json.dumps({'password':'SECRET-abcdef','host':'smtp.invalid','port':465,'tls':True,'username':'alice','unknown':'SECRET-extra'}),'immediate','{}',60,now,now)]
        class DB:
            def fetch_all(db,q):self.queries.append(q);return self.rows
        @contextmanager
        def guard():
            yield
            if self.exit:self.exit()
        self.reader=NotificationChannelsReader(DB());self.service=NotificationChannelsReadService(self.reader,authorize=lambda r:self.allowed,config_authorize=lambda r:self.config,current=lambda:True,lifecycle_guard=guard)
        self.request=Request('GET','/api/notify/channels')
    def test_exact_redacted_shape_no_suffix(self):
        response=self.service.handle(self.request);self.assertEqual(response.status,200);item=response.json()['channels'][0]
        self.assertEqual(item['config'],{'password':'__masked__','host':'smtp.invalid','port':465,'tls':True,'username':'alice'})
        self.assertEqual(item['secret_keys'],['password']);self.assertIsNone(item['filter']['task_ids'])
        self.assertNotIn(b'SECRET',response.body);self.assertNotIn(b'abcdef',response.body)
        self.assertEqual(item['created_at'],'2026-01-01T00:00:00Z')
    def test_unknown_and_nested_secrets_never_echo(self):
        self.assertEqual(redacted_config('newkind',{'password':'secret'}),{})
        cfg=redacted_config('webhook',{'url':'https://token.invalid','headers':{'Authorization':'secret'},'body_template':'secret','method':'POST'})
        self.assertEqual(cfg,{'url':'__masked__','headers':'__masked__','method':'POST'})
    def test_url_risks_and_typed_fields_masked(self):
        for value in ('https://u:p@host','https://host?key=secret','https://host/token','https://host/#secret'):
            self.assertEqual(redacted_config('telegram',{'base_url':value})['base_url'],'__masked__')
        self.assertEqual(redacted_config('telegram',{'base_url':'https://host/'})['base_url'],'https://host/')
        self.assertEqual(redacted_config('email',{'port':True})['port'],'__masked__')
    def test_final_config_revocation(self):
        self.exit=lambda:setattr(self,'config',False)
        self.assertEqual(self.service.handle(self.request).status,403)
    def test_serialization_revokes_original_grant(self):
        original=module.json_response
        def serialize(*a):
            response=original(*a);self.allowed=False;return response
        with patch.object(module,'json_response',serialize):self.assertEqual(self.service.handle(self.request).status,403)
    def test_bound_sentinel_overflow_and_order_refuse(self):
        row=self.rows[0];self.rows=[(False,*row[1:])];self.assertEqual(self.service.handle(self.request).status,500)
        self.rows=[row,row];self.assertEqual(self.service.handle(self.request).status,500)
        self.assertIn('SUM(octet_length',self.queries[0].sql);self.assertEqual(self.queries[0].params,(501,1048576))
    def test_no_reads_without_authority_no_writes(self):
        self.config=False;self.assertEqual(self.service.handle(self.request).status,403);self.assertFalse(self.queries)
        self.assertEqual(self.service.handle(Request('PATCH','/api/notify/channels')).status,400)
    def test_source_filter_null_empty_and_values(self):
        row=list(self.rows[0]);row[7]='{"Task_ids":[5],"asset_ids":[],"on_status_change":true}';self.rows=[tuple(row)]
        value=self.service.handle(self.request).json()['channels'][0]['filter']
        self.assertEqual(value['task_ids'],[5]);self.assertEqual(value['asset_ids'],[]);self.assertTrue(value['on_status_change'])
