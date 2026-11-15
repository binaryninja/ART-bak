"""Independent task chat scope/persistence review, only inert providers/sinks."""
import tempfile,unittest
from contextlib import contextmanager
from dataclasses import replace
from artex.task_chat import TaskChatCore,TaskChatScope
from artex.task_chat_dispatch import TaskChatDispatcher
from artex.agent.chat import ChatBinding
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from test_chat_runtime import MODEL,ALLOW,Provider,text

@contextmanager
def guard(scope):yield

class TaskPersistenceReview(unittest.TestCase):
    def make(self,adapter):
        self.scope=TaskChatScope(1,9,0,2,'revision-a',MODEL,'system')
        self.provider=Provider([text('old result')])
        return TaskChatCore(resolve=lambda task,seg:self.scope,current=lambda scope:scope==self.scope,lifecycle_guard=guard,
            transcript=adapter,provider_resolver=lambda scope,model:ChatBinding(model,self.provider),gate=ALLOW)

    def test_durable_duplicate_cannot_replay_other_scope_revision(self):
        with tempfile.TemporaryDirectory() as root:
            core=self.make(ChatTranscriptAdapter(TranscriptStore(root)))
            core.run(1,'client','hello')
            self.scope=replace(self.scope,run_id=3,revision='revision-b')
            # Fresh core mimics restart; in-memory dispatcher receipts cannot fence it.
            restarted=TaskChatCore(resolve=core.resolve,current=core.current,lifecycle_guard=guard,transcript=core.transcript,
                provider_resolver=core.provider_resolver,gate=ALLOW)
            with self.assertRaises(Exception):restarted.run(1,'client','hello')
            self.assertEqual(len(self.provider.calls),1)

    def test_activity_sink_wrong_exploration_rejected_before_publication(self):
        from test_chat_runtime import Transcript
        core=self.make(Transcript());writes=[]
        class WrongSink:
            task_id=1;exploration_id=10
            def append_many(self,items):writes.extend(items);return [1]*len(items)
        dispatcher=TaskChatDispatcher(core,lambda scope:WrongSink(),single_process=True)
        try:
            with self.assertRaises(Exception):dispatcher.submit(core.prepare(1),'client','hello')
            self.assertEqual(writes,[])
        finally:dispatcher.close(cancel=True,timeout=2)
