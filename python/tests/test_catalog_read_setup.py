"""Optional catalog read with actual JWT, service, reader and inert rows."""
import copy,json,unittest
import test_intercept_read_setup as transport
import test_chat_mentions as mentions
from artex.server.catalog_read_setup import bind_catalog_reads
from artex.server.chat_mentions import PreparedMentionRead

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=transport.Tests();self.t.setUp();self.addCleanup(self.t.doCleanups)
        self.m=mentions.Tests();self.m.setUp();self.addCleanup(self.m.doCleanups)
        self.allowed=True;self.grants=[];self.bundle=self.bind()
    def bind(self):return bind_catalog_reads(self.t.original,mentions_service=self.m.service,authorize_global=lambda r,s,q:self.grants.append(q) or self.allowed)
    def request(self,**kwargs):return self.t.request('/api/chat/mentions',application=self.bundle.application,**kwargs)
    def test_explicit_inert_jwt_and_same_selection(self):
        self.assertFalse(self.m.queries);self.assertFalse(self.grants)
        self.assertEqual(self.t.request('/api/chat/mentions',application=self.t.original)[0],404)
        self.assertEqual(self.request(token=False)[0],401);self.assertFalse(self.m.queries)
        status,body=self.request();self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['items'][0]['id'],12);self.assertEqual(len(self.m.queries),1)
        self.assertEqual(len({id(q) for q in self.grants+self.m.selections}),1)
    def test_no_default_or_mutating_route_and_conflict_atomic(self):
        self.assertEqual(self.request(method='POST')[0],405)
        self.t.original.router.add('HEAD','/api/{kind}/mentions',lambda r,p:None)
        before=tuple(self.t.original.router._routes)
        with self.assertRaises(ValueError):self.bind()
        self.assertEqual(tuple(self.t.original.router._routes),before);self.assertFalse(self.m.queries)
    def test_postfetch_and_header_global_revocation(self):
        self.m.hook=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.request()[0],403)
        self.allowed=True;self.m.hook=None
        with self.assertRaises(RuntimeError):self.request(start=lambda:setattr(self,'allowed',False))
    def test_all_lookahead_assets_retained_after_headers(self):
        self.m.rows=[('ip',n,'synthetic','') for n in range(30,9,-1)]
        old=self.m.reader;seen=[];asset_allowed=[True]
        from artex.db.chat_mentions import ChatMentionReader
        self.m.reader=ChatMentionReader(old.executor,old.scope,authorize=old.authorize,
            asset_authorize=lambda s,q,ids:seen.append(ids) or (asset_allowed[0] or 10 not in ids),current=old.current,lifecycle_guard=old.guard)
        from artex.server.chat_mentions import ChatMentionService
        self.m.service=ChatMentionService(self.m.reader,authorize=lambda *a:True)
        self.bundle=self.bind()
        with self.assertRaises(RuntimeError):self.request(start=lambda:asset_allowed.__setitem__(0,False))
        self.assertIn(tuple(range(30,9,-1)),seen);self.assertEqual(len(self.m.queries),1)
    def test_forged_copied_modified_receipts_refused(self):
        from artex.server.core import Request
        receipt=self.m.service.prepare(Request('GET','/api/chat/mentions'))
        self.m.service.validate_prepared(receipt)
        for forged in (copy.copy(receipt),PreparedMentionRead(receipt.response,receipt.selection,receipt.asset_ids)):
            with self.assertRaises(PermissionError):self.m.service.validate_prepared(forged)
        receipt.response.headers['private']='altered'
        with self.assertRaises(PermissionError):self.m.service.validate_prepared(receipt)
    def test_auth_and_original_service_pins_replaced_during_callback(self):
        self.m.hook=lambda:setattr(self.t.original.router,'authorize',lambda r:True)
        self.assertEqual(self.request()[0],403)

    def test_real_router_declaration_is_visible_to_static_inventory(self):
        from pathlib import Path
        import tempfile
        import test_route_inventory as scanner
        source=Path(__file__).resolve().parents[1]/'artex/server/catalog_read_setup.py'
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);server=root/'python/artex/server';server.mkdir(parents=True)
            (server/'catalog_read_setup.py').write_text(source.read_text())
            rows,_=scanner.audit.python_inventory(root)
        self.assertEqual([(r['method'],r['path'],r['public']) for r in rows],[('GET','/api/chat/mentions',False)])
