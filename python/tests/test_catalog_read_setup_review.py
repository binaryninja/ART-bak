"""Independent issued catalog receipt and final transport grant review."""
import unittest,copy
from artex.server.core import Request
from artex.server.catalog_read_setup import bind_catalog_reads
from artex.db.chat_mentions import ChatMentionReader
from artex.server.chat_mentions import ChatMentionService
import test_catalog_read_setup as fixture

class CatalogSetupReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_final_current_callback_cannot_revoke_outer_global_after_headers(self):
        f=self.f;old=f.m.reader;state={'after':False,'count':0}
        def current(scope):
            if state['after']:
                state['count']+=1
                if state['count']==4:f.allowed=False
            return True
        f.m.reader=ChatMentionReader(old.executor,old.scope,authorize=old.authorize,asset_authorize=old.asset_authorize,current=current,lifecycle_guard=old.guard)
        f.m.service=ChatMentionService(f.m.reader,authorize=lambda *a:True);f.bundle=f.bind()
        with self.assertRaises(RuntimeError):f.request(start=lambda:state.__setitem__('after',True))
        self.assertGreaterEqual(state['count'],4)
    def test_receipt_asset_tuple_replacement_denied(self):
        f=self.f;f.m.rows=[('ip',n,'x','') for n in range(30,9,-1)]
        receipt=f.m.service.prepare(Request('GET','/api/chat/mentions'))
        self.assertEqual(len(receipt.asset_ids),21)
        object.__setattr__(receipt,'asset_ids',receipt.asset_ids[:20])
        with self.assertRaises(PermissionError):f.m.service.validate_prepared(receipt)
    def test_receipt_selection_and_response_replacement_denied(self):
        f=self.f;receipt=f.m.service.prepare(Request('GET','/api/chat/mentions'))
        object.__setattr__(receipt,'selection',copy.copy(receipt.selection))
        with self.assertRaises(PermissionError):f.m.service.validate_prepared(receipt)
    def test_postheader_executor_swap_withholds_body(self):
        f=self.f
        with self.assertRaises(RuntimeError):f.request(start=lambda:setattr(f.m.reader,'executor',object()))
    def test_get_parameterized_conflict_rejects_without_mutation(self):
        f=self.f;f.t.original.router.add('GET','/api/chat/{item}',lambda r,p:None)
        before=tuple(f.t.original.router._routes)
        with self.assertRaises(ValueError):f.bind()
        self.assertEqual(tuple(f.t.original.router._routes),before);self.assertFalse(f.m.queries)
