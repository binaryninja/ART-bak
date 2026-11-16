"""Independent metadata-only mutation review using an inert transactional store."""
import unittest,json,copy
from contextlib import contextmanager
from artex.server.core import Request
import test_notification_channel_metadata as fixture

class ChannelMetadataReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_forbidden_and_duplicate_fields_before_any_sql(self):
        f=self.f
        for raw in ('{"config":null}','{"kind":"email"}','{"enabled":false,"enabled":true}','{"filter":{"task_ids":[1],"TASK_IDS":[2]}}'):
            with self.subTest(raw=raw):
                response=f.service.handle(Request('PATCH','/api/notify/channels/7',body=raw.encode()))
                self.assertEqual(response.status,400);self.assertFalse(f.queries)
    def test_filter_null_omitted_and_empty_are_distinct(self):
        f=self.f;f.row['filter']='{"min_severity":"high","task_ids":[9]}'
        before=f.row['filter'];self.assertEqual(f.request({'filter':None}).status,200);self.assertEqual(f.row['filter'],before)
        self.assertEqual(f.request({'filter':{}}).status,200)
        value=json.loads(f.row['filter']);self.assertEqual(value['min_severity'],'');self.assertIsNone(value['task_ids'])
        self.assertEqual(f.request({'filter':{'task_ids':[]}}).status,200)
        self.assertEqual(json.loads(f.row['filter'])['task_ids'],[])
    def test_already_disabled_does_not_rewrite_delivery_history(self):
        f=self.f;f.row['enabled']=False
        self.assertEqual(f.request({'enabled':False,'rate_per_min':0}).status,200)
        self.assertEqual(f.delivery,['pending','sending','sent'])
        self.assertFalse(any('UPDATE notification_deliveries' in q.sql for q in f.queries))
        self.assertIn('FOR UPDATE',f.queries[0].sql)
    def test_revoke_after_row_lock_prevents_all_writes(self):
        f=self.f;read=f.store.executor.fetch_all
        def revoked(q):
            result=read(q);f.request_allowed=False;return result
        f.store.executor.fetch_all=revoked
        self.assertEqual(f.request({'enabled':False}).status,403)
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in f.queries));self.assertTrue(f.row['enabled'])
    def test_commit_ack_uncertain_executes_only_once(self):
        f=self.f;f.fail_ack=True
        response=f.request({'enabled':False})
        self.assertEqual(response.status,503);self.assertFalse(response.json()['retry'])
        self.assertFalse(f.row['enabled']);self.assertEqual(f.delivery,['skipped','skipped','sent'])
        self.assertEqual(sum(q.sql.startswith('UPDATE notification_channels') for q in f.queries),1)
        self.assertEqual(sum(q.sql.startswith('UPDATE notification_deliveries') for q in f.queries),1)
    def test_guard_exit_revocation_preserves_committed_effect_but_no_success(self):
        f=self.f
        @contextmanager
        def revoked_guard():
            yield
            f.request_allowed=False
        f.guard=revoked_guard
        result=f.request({'name':'committed'})
        self.assertEqual(result.status,503);self.assertEqual(f.row['name'],'committed')
        self.assertFalse(result.json()['retry'])
