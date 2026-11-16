import unittest
from datetime import datetime,timezone
from artex.db.conversation_activity import ConversationActivityStore,ActivityReadLimits,ActivityUnavailable
from test_conversation_activity import Fake
from artex.server.core import Request
import test_chat_http as http

NOW=datetime.now(timezone.utc)
def row(value,id=1):return (id,'','','','',False,value,NOW,None,0,None,None)

class Tests(unittest.TestCase):
    def store(self,rows):
        self.db=Fake([rows]);return ConversationActivityStore(self.db,7,read_limits=ActivityReadLimits(8,16,8))
    def test_exact_utf8_row_and_page_boundary_preserves_fields(self):
        store=self.store([row('é'*4),row('é'*4,2)])
        items,cursor=store.list(0,2)
        self.assertEqual([x.summary for x in items],['é'*4]*2);self.assertEqual(cursor,2)
        self.assertEqual(items[0].output_tokens,0);self.assertIsNone(items[0].input_tokens)
        query=self.db.calls[0][1];self.assertEqual(query.params,(7,0,2,8,16))
        for fragment in ('octet_length','MAX(row_bytes) OVER()','SUM(row_bytes) OVER()','ELSE NULL','LIMIT $3'):
            self.assertIn(fragment,query.sql)
        self.assertNotIn('detail',query.sql)
    def test_oversize_row_total_or_sentinel_never_partial_success(self):
        for rows in ([row('é'*4+'x')],[row('é'*4),row('é'*4,2),row('x',3)],[row(None)]):
            with self.subTest(rows=rows),self.assertRaises(ActivityUnavailable):self.store(rows).list(0,3)
    def test_lookahead_counts_toward_page_bytes(self):
        with self.assertRaises(ActivityUnavailable):self.store([row('x'*8,3),row('x'*8,2),row('x',1)]).page(0,2)
        store=self.store([row('x'*7,3),row('x'*7,2),row('xx',1)])
        items,more=store.page(0,2);self.assertTrue(more);self.assertEqual([x.id for x in items],[2,3])
        self.assertEqual(self.db.calls[0][1].params,(7,0,3,8,16))
    def test_detail_utf8_exact_null_empty_missing_and_overflow(self):
        for rows,expected in ([('é'*4,)],'é'*4),([('',)],''),([], ''):
            store=self.store(rows);self.assertEqual(store.detail(1),expected)
            self.assertEqual(self.db.calls[0][1].params,(1,7,8))
            self.assertIn('CASE WHEN octet_length',self.db.calls[0][1].sql)
            self.assertIn("COALESCE(detail,'')",self.db.calls[0][1].sql)
        for rows in ([(None,)],[('é'*4+'x',)],[('a',),('b',)]):
            with self.assertRaises(ActivityUnavailable):self.store(rows).detail(1)
    def test_invalid_limits_and_rebinding(self):
        for limits in (ActivityReadLimits(True,16,8),ActivityReadLimits(9,8,8),ActivityReadLimits(8,16,0),ActivityReadLimits(8,16777217,8)):
            with self.assertRaises(ValueError):ConversationActivityStore(Fake(),7,read_limits=limits)
        store=self.store([]);store.read_limits=ActivityReadLimits(8,16,8)
        with self.assertRaises(ActivityUnavailable):store.list()
        self.assertEqual(self.db.calls,[])
    def test_http_overflow_unavailable_and_revocation_denied(self):
        for revoke in (False,True):
            f=http.ChatHTTPTests();f.setUp();self.addCleanup(f.doCleanups);allowed=[True]
            f.service.authorize=lambda r,c:allowed[0]
            db=Fake([[row(None)]])
            original=db.fetch_all
            def fetch(q):
                result=original(q)
                if revoke:allowed[0]=False
                return result
            db.fetch_all=fetch
            f.service.activities=lambda i:ConversationActivityStore(db,i,read_limits=ActivityReadLimits(8,16,8))
            response=f.router.dispatch(Request('GET','/api/conversations/7/messages',{'Authorization':'fixture'}))
            self.assertEqual(response.status,403 if revoke else 503)
            self.assertNotIn(b'private',response.body)
    def test_runtime_setup_explicit_limits_reach_actual_activity_store(self):
        import test_chat_setup as setup
        from artex.server.chat_setup import build_conversation_runtime
        from artex.agent.runtime import Grant
        f=setup.ChatSetupTests();f.setUp();self.addCleanup(f.doCleanups)
        limits=ActivityReadLimits(8,16,8)
        bundle=build_conversation_runtime(f.db,f.profiles,transcript_root=f.temp.name,
            authorize=lambda *a:True,gate=lambda *a:Grant(True,True,True),tools=(),system_prompt=lambda c:'fixture',
            single_process=True,activity_read_limits=limits)
        self.addCleanup(lambda:bundle.close(cancel=True,timeout=2))
        self.assertIs(bundle.service.activities(1).read_limits,limits)
