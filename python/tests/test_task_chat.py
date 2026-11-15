import tempfile,unittest
from contextlib import contextmanager
from dataclasses import replace
from threading import RLock
from artex.task_chat import TaskChatCore,TaskChatScope
from artex.agent.chat import ChatBinding,ChatBusy,ChatPersistenceError,ChatUnavailable
from artex.agent.runtime import RunStopped
from artex.transcripts import TranscriptStore
from artex.chat_transcripts import ChatTranscriptAdapter
from test_chat_runtime import Transcript,Provider,MODEL,ALLOW,text

class ScopedTranscript(Transcript):
    def __init__(self):super().__init__();self.scopes={}
    def begin_scoped(self,session,client,user,identity):
        key=(session,client)
        if key in self.scopes and self.scopes[key]!=identity:raise ValueError('scope changed')
        self.scopes[key]=identity
        return self.begin(session,client,user)

class TaskChatTests(unittest.TestCase):
    def setUp(self):
        self.scope=TaskChatScope(1,9,0,2,'revision-a',MODEL,'Explicit human-interface system')
        self.live=True;self.tx=ScopedTranscript();self.provider=Provider([text('received')]);self.lock=RLock();self.events=[]
        @contextmanager
        def guard(scope):
            with self.lock:
                self.events.append('enter')
                try:yield
                finally:self.events.append('exit')
        self.guard=guard
    def core(self,**kwargs):
        return TaskChatCore(resolve=lambda task,segment:self.scope,current=lambda scope:self.live and scope==self.scope,
                            lifecycle_guard=self.guard,transcript=kwargs.pop('transcript',self.tx),provider_resolver=lambda scope,model:ChatBinding(model,self.provider),gate=ALLOW,**kwargs)
    def test_actual_bounded_consumption_and_canonical_session(self):
        core=self.core();result=core.run(1,'client','hello')
        self.assertTrue(result.persisted);self.assertEqual(result.result.text,'received')
        self.assertIn('exp9-main',self.tx.history);self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(self.provider.calls[0]['messages'][-1]['content'][0]['text'],'hello')
        self.assertFalse(core.running(1));self.assertEqual(self.events.count('enter'),self.events.count('exit'))
    def test_segment_session(self):
        self.scope=replace(self.scope,segment=3)
        self.core().run(1,'client','hello',segment=3)
        self.assertIn('exp9-main-s3',self.tx.history)
    def test_authorization_scope_mismatch_before_admission(self):
        core=self.core()
        with self.assertRaises(ChatUnavailable):core.run(2,'client','hello')
        with self.assertRaises(ChatUnavailable):core.run(1,'client','hello',segment=2)
        self.assertEqual(self.tx.history,{})
    def test_deleted_or_revision_changed_before_admission(self):
        self.live=False
        with self.assertRaises(RunStopped):self.core().run(1,'client','hello')
        self.assertEqual(self.provider.calls,[]);self.assertEqual(self.tx.history,{})
    def test_revision_change_during_provider_prevents_commit_retains_owner(self):
        core=self.core();self.provider.hook=lambda:setattr(self,'live',False)
        with self.assertRaises(ChatPersistenceError):core.run(1,'client','hello')
        self.assertEqual(self.tx.commits,[]);self.assertTrue(core.running(1))
        self.live=True
        with self.assertRaises(ChatBusy):core.run(1,'other','hello')
    def test_cancel_inflight_commits_cancelled_and_allows_later_turn(self):
        core=self.core();self.provider.hook=lambda:core.cancel(1)
        outcome=core.run(1,'client','hello')
        self.assertTrue(outcome.persisted);self.assertEqual(outcome.result.reason,'cancelled');self.assertFalse(core.running(1))
    def test_concurrent_reentrant_turn_rejected(self):
        core=self.core();observed=[]
        def hook():
            with self.assertRaises(ChatBusy):core.run(1,'other','hello')
            observed.append(True)
        self.provider.hook=hook;core.run(1,'client','hello');self.assertEqual(observed,[True])
    def test_real_temp_transcript_and_reuse_no_reexecution(self):
        with tempfile.TemporaryDirectory() as root:
            store=TranscriptStore(root);adapter=ChatTranscriptAdapter(store)
            core=self.core(transcript=adapter)
            result=core.run(1,'client','hello');again=core.run(1,'client','hello')
            self.assertTrue(result.persisted);self.assertTrue(again.reused);self.assertTrue(again.persisted)
            self.assertEqual(again.result.text,'received')
            self.assertEqual(len(self.provider.calls),1);self.assertEqual(len(store.read('exp9-main')),2)
