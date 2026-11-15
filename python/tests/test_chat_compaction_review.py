"""Independent inert summary-to-main lifecycle regressions."""
from dataclasses import replace
import unittest
from artex.agent.chat import ChatAgent, ChatBinding
from artex.agent.chat_compaction import ChatCompaction
from artex.agent.compaction import Budget
from artex.agent.runtime import Grant
from test_chat_runtime import Transcript, Provider, MODEL, ALLOW, text
from test_agent_streaming import Streaming, START, STOP, end, text as delta


class ChatCompactionReview(unittest.TestCase):
    def setup_parts(self):
        tx=Transcript()
        tx.history['s']=[{'role':'user','content':[{'type':'text','text':'old evidence '*550}]},
                         {'role':'assistant','content':[{'type':'text','text':'old answer '*350}]}]
        return tx, Provider([text('summary'),text('main answer')]), replace(MODEL,window_tokens=4000)

    def test_scope_change_during_final_summary_resolution_never_calls_provider(self):
        self.check_final_resolution(False)

    def test_stream_scope_change_during_final_resolution_never_opens_stream(self):
        self.check_final_resolution(True)

    def check_final_resolution(self,streaming):
        tx, provider, model=self.setup_parts()
        if streaming:
            model=replace(model,streaming=True)
            provider=Streaming([START,delta("summary"),end(),STOP])
        armed=False; resolutions=0
        def gate(context,action):
            nonlocal armed
            if action.name=='compact':armed=True
            return ALLOW(context,action)
        def resolver(expected):
            nonlocal resolutions
            if armed:
                resolutions+=1
                # post-grant live check, SummaryProvider.live, then final binding.
                if resolutions==3:tx.clear()
            return ChatBinding(expected,provider)
        agent=ChatAgent(tx,resolver,gate=gate,compaction=ChatCompaction(Budget(4000,128,1)))
        outcome=agent.run('s','c','current',model=model)
        self.assertTrue(outcome.stale)
        self.assertEqual(provider.calls,[])
        self.assertEqual(outcome.result.usage['input_tokens'],0)

    def test_transformed_main_grant_denial_keeps_summary_cost_and_raw_history(self):
        tx, provider, model=self.setup_parts()
        original=list(tx.history['s']); completes=0
        def gate(context,action):
            nonlocal completes
            if action.name=='complete':
                completes+=1
                if completes==2:return Grant(False,True,True)
            return ALLOW(context,action)
        agent=ChatAgent(tx,lambda m:ChatBinding(m,provider),gate=gate,
                        compaction=ChatCompaction(Budget(4000,128,1)))
        outcome=agent.run('s','c','current',model=model)
        self.assertEqual(outcome.result.reason,'permission_denied')
        self.assertEqual(len(provider.calls),1)
        self.assertEqual(outcome.result.usage['input_tokens'],2)
        self.assertEqual(tx.history['s'][:2],original)
