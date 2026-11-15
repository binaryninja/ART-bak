"""Independent streamed-turn regressions with inert callbacks, no live providers."""
from dataclasses import replace
import unittest
from artex.agent.chat import ChatAgent, ChatBinding
from artex.agent.runtime import Tool
from artex.agent.streaming import StreamingCompletion, StreamProtocolError
import test_agent_streaming as fixture
import test_chat_runtime as chat_fixture
from test_process import Context

class StreamingReview(unittest.TestCase):
    def test_profile_rotation_during_stream_close_cannot_execute_tools(self):
        live=[True];calls=[];tx=chat_fixture.Transcript()
        events=[fixture.START,*fixture.tool_events(),fixture.end('tool_use'),fixture.STOP]
        class Provider:
            def stream(self,context,request):
                try:yield from events
                finally:live[0]=False
        provider=Provider()
        def resolver(model):
            return ChatBinding(model if live[0] else replace(model,identity='rotated'),provider)
        tool=Tool('fixture','inert',{'type':'object'},lambda c,a:calls.append(a) or 'ok',lambda a:True)
        outcome=ChatAgent(tx,resolver,gate=chat_fixture.ALLOW,tools=[tool]).run('s','c','hello',model=replace(chat_fixture.MODEL,streaming=True))
        self.assertEqual(calls,[])
        self.assertEqual(outcome.result.reason,'model_error')

    def test_early_eof_does_not_publish_complete_turn(self):
        provider=fixture.Streaming([fixture.START,fixture.text('partial'),fixture.end()])
        with self.assertRaises(StreamProtocolError):
            StreamingCompletion(provider,check=lambda:None).complete(Context(),{'max_tokens':10})
        self.assertEqual(provider.closed,1)

    def test_close_failure_prevents_successful_completion(self):
        class Provider:
            def stream(self,context,request):
                try:yield from [fixture.START,fixture.text('partial'),fixture.end(),fixture.STOP]
                finally:raise RuntimeError('private cleanup fixture')
        with self.assertRaisesRegex(StreamProtocolError,'cleanup unconfirmed') as caught:
            StreamingCompletion(Provider(),check=lambda:None).complete(Context(),{'max_tokens':10})
        self.assertNotIn('private',str(caught.exception))
