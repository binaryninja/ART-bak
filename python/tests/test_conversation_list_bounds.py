import unittest
from artex.db.conversations import ConversationStore,ConversationReadLimits,ConversationUnavailable
from test_conversations import Executor,ROW
import test_conversation_list_disclosure as fixture

class Tests(unittest.TestCase):
    def test_exact_utf8_and_legacy_projection(self):
        db=Executor();db.rows=[(1,'é','漢',*ROW[3:])]
        store=ConversationStore(db,read_limits=ConversationReadLimits(1,5,5))
        self.assertEqual(store.list()[0].title,'漢')
        self.assertEqual(db.calls[-1].params,(2,1,5,5))
        self.assertIn('COUNT(*) OVER()',db.calls[-1].sql)
        self.assertIn('pinned_at DESC NULLS LAST, updated_at DESC, id DESC',db.calls[-1].sql)
    def test_no_partial_row_count_or_aggregate(self):
        for rows,limits in (([ROW,ROW],ConversationReadLimits(1,100,100)),
                            ([ROW],ConversationReadLimits(2,5,100)),
                            ([ROW,ROW],ConversationReadLimits(2,20,20)),
                            ([(1,None,None,*ROW[3:])],ConversationReadLimits())):
            db=Executor();db.rows=rows
            with self.assertRaises(ConversationUnavailable):ConversationStore(db,read_limits=limits).list()
    def test_config_validation_and_rebinding(self):
        for limits in (ConversationReadLimits(True),ConversationReadLimits(10001),ConversationReadLimits(1,3,2),ConversationReadLimits(1,1,16777217)):
            with self.assertRaises(ValueError):ConversationStore(Executor(),read_limits=limits)
        db=Executor();store=ConversationStore(db);store.read_limits=ConversationReadLimits()
        with self.assertRaises(ConversationUnavailable):store.list()
        self.assertEqual(db.calls,[])
    def test_empty_and_exact_aggregate(self):
        db=Executor();db.rows=[];self.assertEqual(ConversationStore(db).list(),[])
        db.rows=[(1,'a','é',*ROW[3:]),(2,'b','é',*ROW[3:])]
        self.assertEqual(len(ConversationStore(db,read_limits=ConversationReadLimits(2,3,6)).list()),2)
    def test_http_overflow_and_revocation(self):
        f=fixture.Tests();f.setUp();f.db.rows=[(1,None,None,*ROW[3:])]
        self.assertEqual(f.call()[0],503)
        original=f.db.fetch_all
        def fetch(q):r=original(q);f.allowed=False;return r
        f.db.fetch_all=fetch;self.assertEqual(f.call()[0],403)
    def test_runtime_configuration(self):
        from test_chat_setup import ChatSetupTests
        f=ChatSetupTests();f.setUp()
        try:
            from artex.server.chat_setup import build_conversation_runtime
            from artex.agent.runtime import Grant
            limits=ConversationReadLimits(2,8,16)
            runtime=build_conversation_runtime(f.db,f.profiles,transcript_root=f.temp.name,
                authorize=lambda *a:True,gate=lambda *a:Grant(True,True,True),tools=(),
                system_prompt=lambda c:'',single_process=True,conversation_read_limits=limits)
            try:self.assertIs(runtime.service.store.read_limits,limits)
            finally:runtime.close(cancel=True,timeout=3)
        finally:f.doCleanups()
    def test_full_application_metadata_uses_custom_limits(self):
        from test_chat_setup import OneApplicationTests,TestCodec
        from artex.server.chat_setup import build_chat_application
        f=OneApplicationTests();f.setUp()
        try:
            f.bundle.close(cancel=True,timeout=3)
            f.bundle=build_chat_application(f.db,b'x'*32,TestCodec(),conversation_read_limits=ConversationReadLimits(1,1,1),**f.kwargs)
            token=f.request('/api/auth/init',method='POST',data={'password':'fixture-password'})[1]['token']
            status,body=f.request('/api/conversations',token=token)
            self.assertEqual(status,503);self.assertNotIn('conversations',body)
        finally:f.doCleanups()
