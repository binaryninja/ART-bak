"""Independent wrapper boundary regressions; generated fixture records only."""
import unittest
from artex.server.notification_metadata_setup import bind_notification_metadata
import test_notification_metadata_setup as fixtures

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_original_request_mutation_in_global_grant_denies_before_sql(self):
        f=self.f
        def grant(request,scope,proposal):
            request.headers['X-fixture']='changed'
            return True
        f.bundle=bind_notification_metadata(f.t.original,service=f.m.service,authorize_global=grant)
        self.assertEqual(f.request()[0],403)
        self.assertEqual(f.m.queries,[])
    def test_replaced_store_method_denies_before_invocation(self):
        f=self.f;called=[]
        f.m.store.patch=lambda *a,**k:called.append(1)
        self.assertEqual(f.request()[0],403);self.assertEqual(called,[]);self.assertEqual(f.m.queries,[])
    def test_store_row_callback_replaces_original_jwt_before_write(self):
        f=self.f;old=f.m.store.executor.fetch_all
        def fetch(q):
            rows=old(q);f.t.original.router.authorize=lambda r:True;return rows
        f.m.store.executor.fetch_all=fetch
        self.assertEqual(f.request()[0],403);self.assertEqual(f.m.row['name'],'original')
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in f.m.queries))
    def test_store_grant_revoked_after_select_no_update(self):
        f=self.f;old=f.m.store.executor.fetch_all
        def fetch(q):
            rows=old(q);f.m.allowed=False;return rows
        f.m.store.executor.fetch_all=fetch
        self.assertEqual(f.request()[0],403);self.assertEqual(f.m.row['name'],'original')
    def test_postcommit_store_grant_revocation_is_unknown_not_rollback(self):
        f=self.f;f.m.before_commit=lambda:setattr(f.m,'allowed',False)
        status,body=f.request();self.assertEqual(status,503)
        self.assertEqual(f.m.row['name'],'changed');self.assertIn(b'"retry":false',body)
