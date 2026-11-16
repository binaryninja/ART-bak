"""Real JWT/WSGI and actual metadata store, with inert in-memory transactions."""
import json,unittest
from unittest.mock import patch
import test_intercept_read_setup as transport
import test_notification_channel_metadata as metadata
from artex.server.notification_metadata_setup import bind_notification_metadata
from artex.server import notification_channel_metadata as service_module
from artex.server.core import json_response

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.m=metadata.Tests();self.m.setUp();self.addCleanup(self.m.doCleanups)
        self.allowed=True;self.calls=[]
        self.bundle=self.bind()
    def bind(self):
        return bind_notification_metadata(self.t.original,service=self.m.service,
            authorize_global=lambda r,s,p:self.calls.append((r,s,p)) or self.allowed)
    def request(self,values=None,**kwargs):
        return self.t.request('/api/notify/channels/7',method='PATCH',application=self.bundle.application,
                              body=json.dumps(values or {'name':'changed'}).encode(),**kwargs)
    def test_inert_optional_and_metadata_only(self):
        self.assertFalse(self.calls);self.assertFalse(self.m.queries)
        before=tuple(self.t.original.router._routes);cfg=self.m.row['config']
        self.assertEqual(self.t.request('/api/notify/channels/7',method='PATCH',application=self.t.original)[0],404)
        status,body=self.request({'name':'changed','enabled':False})
        self.assertEqual(status,200);self.assertEqual(json.loads(body),{'id':7})
        self.assertEqual(self.m.row['config'],cfg);self.assertEqual(self.m.row['kind'],'email')
        self.assertEqual(self.m.delivery,['skipped','skipped','sent'])
        self.assertEqual(tuple(self.t.original.router._routes),before)
        self.assertEqual(sum(q.sql.startswith('UPDATE notification_channels') for q in self.m.queries),1)
        self.assertEqual(len({id(c[2]) for c in self.calls}),1)
    def test_jwt_then_independent_global_and_service_grants(self):
        self.assertEqual(self.request(token=False)[0],401);self.assertFalse(self.calls);self.assertFalse(self.m.queries)
        self.allowed=False;self.assertEqual(self.request()[0],403);self.assertFalse(self.m.queries)
        self.allowed=True;self.m.request_allowed=False
        self.assertEqual(self.request()[0],403);self.assertFalse(self.m.queries)
    def test_original_store_read_callback_revokes_before_write(self):
        old=self.m.store.executor.fetch_all
        def fetch(q):
            result=old(q);self.allowed=False;return result
        self.m.store.executor.fetch_all=fetch
        self.assertEqual(self.request()[0],403)
        self.assertEqual(self.m.row['name'],'original')
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in self.m.queries))
    def test_postcommit_revocation_never_claims_rollback_or_retry(self):
        self.m.before_commit=lambda:setattr(self,'allowed',False)
        status,body=self.request();self.assertEqual(status,503)
        self.assertEqual(self.m.row['name'],'changed');self.assertFalse(json.loads(body)['retry'])
    def test_final_serialization_and_start_response_revocation(self):
        original=service_module.json_response
        def serialize(*args,**kw):
            response=original(*args,**kw);self.allowed=False;return response
        with patch.object(service_module,'json_response',serialize):
            status,body=self.request()
        self.assertEqual(status,503);self.assertFalse(json.loads(body)['retry'])
        self.allowed=True
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self,'allowed',False))
        self.assertEqual(self.m.row['name'],'changed')
    def test_last_original_store_current_cannot_omit_outer_write_grant(self):
        from artex.db.notification_channel_metadata import ChannelMetadataStore
        from artex.server.notification_channel_metadata import ChannelMetadataService
        old=self.m.store;calls=[]
        def current(scope):
            calls.append(scope)
            # Frozen original adapter: fourth store allow, immediately before UPDATE.
            if len(calls)==12:self.allowed=False
            return True
        self.m.store=ChannelMetadataStore(old.executor,old.scope,authorize=old.authorize,current=current,lifecycle_guard=old.guard)
        self.m.service=ChannelMetadataService(self.m.store,authorize=self.m.service.authorize)
        self.bundle=self.bind()
        self.assertEqual(self.request()[0],403)
        self.assertFalse(any(q.sql.startswith('UPDATE') for q in self.m.queries))
        self.assertEqual(self.m.row['name'],'original')

    def test_last_original_precommit_current_rolls_back_tentative_write(self):
        from artex.db.notification_channel_metadata import ChannelMetadataStore
        from artex.server.notification_channel_metadata import ChannelMetadataService
        old=self.m.store;after_update=[]
        def current(scope):
            if self.m.row['name']=='changed':
                after_update.append(scope)
                # Final original store current after the composed request check.
                if len(after_update)==3:self.allowed=False
            return True
        self.m.store=ChannelMetadataStore(old.executor,old.scope,authorize=old.authorize,current=current,lifecycle_guard=old.guard)
        self.m.service=ChannelMetadataService(self.m.store,authorize=self.m.service.authorize)
        self.bundle=self.bind()
        status,body=self.request()
        self.assertEqual(status,503);self.assertFalse(json.loads(body)['retry'])
        self.assertEqual(sum(q.sql.startswith('UPDATE') for q in self.m.queries),1)
        self.assertEqual(self.m.row['name'],'original')

    def test_unknown_commit_preserved_config_never_in_errors(self):
        self.m.fail_ack=True;status,body=self.request()
        self.assertEqual(status,503);self.assertEqual(json.loads(body)['outcome'],'unknown')
        self.assertNotIn(b'secret',body);self.assertEqual(self.m.row['name'],'changed')
    def test_conflicts_atomic_mixed_prefix_and_unrelated_delegate(self):
        self.t.original.router.add('GET','/unrelated',lambda r,p:json_response(200,{'ok':True}),public=True)
        self.bundle=self.bind()
        self.assertEqual(self.t.request('/unrelated',application=self.bundle.application)[0],200)
        self.t.original.router.add('PATCH','/a{rest}/notify/channels/7',lambda r,p:None)
        before=tuple(self.t.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(tuple(self.t.original.router._routes),before);self.assertFalse(self.m.queries)
    def test_original_bindings_cannot_be_replaced(self):
        self.t.original.router.authorize=lambda r:True
        self.assertEqual(self.request()[0],403);self.assertFalse(self.m.queries)
    def test_unsupported_payload_never_mutates(self):
        status,body=self.request({'config':{'password':'not-saved'}})
        self.assertEqual(status,400);self.assertFalse(self.m.queries);self.assertNotIn(b'password',body)
