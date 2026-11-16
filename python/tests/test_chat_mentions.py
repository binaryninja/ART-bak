"""Synthetic catalog SQL/DTO tests only; no mentions expanded or sent."""
import base64,json,unittest
from contextlib import contextmanager
from urllib.parse import urlencode
from unittest.mock import patch
from artex.db.chat_mentions import ChatMentionReader,CatalogScope,MentionSelection,SQL,decode_cursor
from artex.server.chat_mentions import ChatMentionService
from artex.server.core import Request
from artex.server import chat_mentions as service_module

class Tests(unittest.TestCase):
    def setUp(self):
        self.rows=[('finding',12,'Synthetic finding','high · open · synthetic'),('company',7,'Example','example')]
        self.queries=[];self.allowed=True;self.assets=True;self.request_allowed=True;self.hook=None;self.exit_hook=None;self.selections=[]
        class DB:
            def fetch_all(db,q):
                self.queries.append(q)
                if self.hook:self.hook()
                return self.rows
        @contextmanager
        def guard():
            yield
            if self.exit_hook:self.exit_hook()
        self.reader=ChatMentionReader(DB(),CatalogScope('global-catalog','revision1'),authorize=lambda s,q:self.allowed,
            asset_authorize=lambda s,q,ids:self.assets,current=lambda s:True,lifecycle_guard=guard)
        self.service=ChatMentionService(self.reader,authorize=lambda r,s,q:self.selections.append(q) or self.request_allowed)
    def request(self,**query):return self.service.handle(Request('GET','/api/chat/mentions'+('?' +urlencode(query) if query else '')))
    def test_global_source_page_and_copied_dto(self):
        result=self.request();self.assertEqual(result.status,200)
        self.assertEqual(result.json()['items'][0]['id'],12);self.assertNotIn('next_cursor',result.json())
        self.assertEqual(len(self.queries),1);self.assertEqual(len({id(s) for s in self.selections}),1)
        self.rows[0]=('finding',12,'Changed','Changed')
        self.assertEqual(result.json()['items'][0]['label'],'Synthetic finding')
    def test_literal_search_escaping_and_source_rune_limit(self):
        self.rows=[];self.assertEqual(self.request(q='  %_\\ literal  ').status,200)
        self.assertEqual(self.queries[-1].params[1:3],('%_\\ literal','%\\%\\_\\\\ literal%'))
        self.assertEqual(self.request(q='é'*200).status,200)
        self.assertEqual(self.request(q='é'*201).status,400)
    def test_keyset_cursor_exact_kind_query_scope_and_numeric_order(self):
        self.rows=[('finding',n,'label','description') for n in range(100,79,-1)]
        response=self.request(kind='finding');self.assertEqual(response.status,200)
        page=response.json();self.assertEqual(len(page['items']),20)
        s=MentionSelection('finding','',page['next_cursor']);self.assertEqual(decode_cursor(s),(81,'finding',False))
        self.rows=[('finding',80,'label','description')]
        self.assertEqual(self.request(kind='finding',cursor=page['next_cursor']).status,200)
        self.assertEqual(self.queries[-1].params[3:],(81,'finding',False))
        self.assertEqual(self.request(kind='company',cursor=page['next_cursor']).status,400)
        self.assertEqual(self.request(kind='finding',q='different',cursor=page['next_cursor']).status,400)
    def test_exact_id_before_higher_numeric_ids(self):
        self.rows=[('finding',7,'exact',''),('company',100,'other','')]
        self.assertEqual(self.request(q='7').status,200)
        self.rows.reverse();self.assertEqual(self.request(q='7').status,500)
    def test_strict_selectors_and_cursor_types_never_query(self):
        for target in ('?kind=unknown','?q=x&q=y','?task=9','?cursor=bad!','?q=%ZZ'):
            self.assertEqual(self.service.handle(Request('GET','/api/chat/mentions'+target)).status,400)
        c=dict(id=True,kind='finding',exact=False,scope='',query='')
        value=base64.urlsafe_b64encode(json.dumps(c).encode()).rstrip(b'=').decode()
        self.assertEqual(self.request(cursor=value).status,400);self.assertFalse(self.queries)
    def test_row_and_text_bounds_fail_whole_read(self):
        for rows in ([('finding',n,'','') for n in range(23,1,-1)],[('finding',True,'','')],[('finding',5,'x'*161,'')],[('finding',5,'','x'*241)]):
            self.rows=rows;self.assertEqual(self.request().status,500)
        self.assertIn('left(label,160)',SQL);self.assertEqual(SQL.count('LIMIT 21'),4)
    def test_global_asset_and_request_grants_independent(self):
        self.allowed=False;self.assertEqual(self.request().status,403);self.assertFalse(self.queries)
        self.allowed=True;self.request_allowed=False;self.assertEqual(self.request().status,403);self.assertFalse(self.queries)
        self.request_allowed=True;self.assets=False;self.assertEqual(self.request().status,403);self.assertFalse(self.queries)
    def test_postfetch_original_callback_swap_and_guardexit_denial(self):
        def change():self.allowed=False;self.reader.authorize=lambda *a:True
        self.hook=change;self.assertEqual(self.request().status,403)
        self.reader.authorize=self.reader._pins[4];self.allowed=True;self.hook=None
        self.exit_hook=lambda:setattr(self,'request_allowed',False)
        self.assertEqual(self.request().status,403)
    def test_after_serialization_and_asset_callback_source_revocation(self):
        original=service_module.json_response
        def project(*a,**kw):
            response=original(*a,**kw);self.request_allowed=False;return response
        with patch.object(service_module,'json_response',project):self.assertEqual(self.request().status,403)
        self.request_allowed=True
        self.reader.asset_authorize=self.reader._pins[5] # original remains pinned
        self.exit_hook=lambda:setattr(self,'allowed',False)
        self.assertEqual(self.request().status,403)
