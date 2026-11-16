"""Independent bounded catalog search review; no expansion or messages."""
import unittest,json,base64
from unittest.mock import patch
from artex.server import chat_mentions
import test_chat_mentions as fixture

class ChatMentionsReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_exact_numeric_id_precedes_larger_id_and_kind_ties(self):
        f=self.f;f.rows=[('finding',2,'match','x'),('company',100,'x','x'),('finding',100,'x','x')]
        response=f.request(q='2');self.assertEqual(response.status,200)
        self.assertEqual([x['id'] for x in response.json()['items']],[2,100,100])
        f.rows=[f.rows[0],f.rows[2],f.rows[1]]
        self.assertEqual(f.request(q='2').status,500)
    def test_wrong_kind_and_duplicate_ids_same_kind_are_refused(self):
        f=self.f
        self.assertEqual(f.request(kind='asset').status,500)
        f.rows=[('finding',7,'x','x')]*2
        self.assertEqual(f.request(kind='finding').status,500)
    def test_lookahead_assets_are_authorized_before_pagination_metadata(self):
        f=self.f;f.rows=[('ip',n,'x','x') for n in range(30,9,-1)];seen=[]
        # Configure a new explicit original reader rather than swap a pinned callback.
        from artex.db.chat_mentions import ChatMentionReader
        from artex.server.chat_mentions import ChatMentionService
        r=f.reader
        def assets(scope,selection,ids):seen.append(ids);return 10 not in ids
        reader=ChatMentionReader(r.executor,r.scope,authorize=r.authorize,asset_authorize=assets,current=r.current,lifecycle_guard=r.guard)
        f.service=ChatMentionService(reader,authorize=lambda *a:True)
        response=f.request(kind='ip');self.assertEqual(response.status,403)
        self.assertIn(tuple(range(30,9,-1)),seen);self.assertNotIn(b'next_cursor',response.body)
    def test_serialization_revokes_asset_visibility(self):
        f=self.f;f.rows=[('ip',9,'private','x')];original=chat_mentions.json_response
        def serialized(*a):
            result=original(*a);f.assets=False;return result
        with patch.object(chat_mentions,'json_response',serialized):
            response=f.request();self.assertEqual(response.status,403);self.assertNotIn(b'private',response.body)
    def test_guard_exit_request_revocation_withholds_results(self):
        f=self.f;f.exit_hook=lambda:setattr(f,'request_allowed',False)
        response=f.request();self.assertEqual(response.status,403);self.assertNotIn(b'Synthetic',response.body)
    def test_cursor_numeric_and_scope_confusion_refused_before_sql(self):
        f=self.f
        base={'id':9,'kind':'finding','exact':False,'scope':'finding','query':'literal'}
        for change in ({'id':True},{'id':2**63},{'exact':0},{'query':'other'},{'scope':'asset'}):
            value=dict(base,**change);encoded=base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()
            with self.subTest(change=change):self.assertEqual(f.request(kind='finding',q='literal',cursor=encoded).status,400)
        self.assertFalse(f.queries)
