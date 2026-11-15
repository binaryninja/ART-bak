from copy import deepcopy
from dataclasses import replace
import unittest
from artex.agent.chat import ChatAgent,ChatBinding
from artex.agent.chat_compaction import ChatCompaction
from artex.agent.compaction import Budget
from artex.agent.runtime import Limits,RunStopped
from test_chat_runtime import Transcript,Provider,MODEL,ALLOW,text
from test_agent_streaming import Streaming,START,STOP,end,text as delta

class Integration(unittest.TestCase):
    def setUp(self):
        self.tx=Transcript();self.tx.history['s']=[{'role':'user','content':[{'type':'text','text':'old evidence '*550}]},
            {'role':'assistant','content':[{'type':'text','text':'old answer '*350}]}]
        self.raw=deepcopy(self.tx.history['s']);self.captures=[];self.actions=[]
        self.model=replace(MODEL,window_tokens=4000)
        self.config=ChatCompaction(Budget(4000,128,1))
        self.provider=Provider([text('historical observations'),text('answer')])
    def agent(self,**kwargs):
        def gate(c,a):self.actions.append(a);return ALLOW(c,a)
        return ChatAgent(self.tx,lambda m:ChatBinding(m,self.provider),gate=kwargs.pop('gate',gate),
            capture=self.captures.append,compaction=self.config,**kwargs)
    def test_actual_request_compacts_raw_transcript_stays(self):
        result=self.agent().run('s','c','current',model=self.model,system='Unchanged policy')
        self.assertEqual(result.result.reason,'completed')
        self.assertEqual(len(self.provider.calls),2)
        self.assertEqual(self.provider.calls[1]['system'],['Unchanged policy'])
        self.assertEqual(self.captures[0].request,self.provider.calls[1])
        self.assertNotEqual(self.provider.calls[1]['messages'][:2],self.raw)
        self.assertEqual(self.tx.history['s'][:2],self.raw)
        self.assertEqual(result.result.usage['input_tokens'],4)
        self.assertEqual(result.result.usage['output_tokens'],2)
        self.assertEqual([a.name for a in self.actions],['complete','compact','complete'])
    def test_failure_after_summary_charge(self):
        self.provider.responses=[text('summary'),RuntimeError('main failed')]
        result=self.agent().run('s','c','current',model=self.model)
        self.assertEqual(result.result.reason,'model_error')
        self.assertEqual(result.result.usage['input_tokens'],2)
        self.assertEqual(result.result.usage['output_tokens'],1)
    def test_summary_refusal_still_charged(self):
        m,_,u=text('refusal');self.provider.responses=[(m,'refusal',u)]
        result=self.agent().run('s','c','current',model=self.model)
        self.assertEqual(result.result.reason,'model_error');self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(result.result.usage['input_tokens'],2)
    def test_summary_exhausts_no_main_call(self):
        m,stop,_=text('summary');self.provider.responses=[(m,stop,{'input_tokens':129,'output_tokens':1})]
        result=self.agent(limits=Limits(max_tokens=130)).run('s','c','current',model=self.model)
        self.assertEqual(result.result.reason,'token_budget');self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(result.result.usage['input_tokens'],129)
    def test_small_remaining_allowance_stops_before_summary(self):
        result=self.agent(limits=Limits(max_tokens=100)).run('s','c','current',model=self.model)
        self.assertEqual(result.result.reason,'token_budget');self.assertFalse(self.provider.calls)
    def test_cancel_during_summary_retains_charge(self):
        class Token:cancelled=False
        token=Token();self.provider.hook=lambda:setattr(token,'cancelled',True)
        result=self.agent().run('s','c','current',model=self.model,token=token)
        self.assertEqual(result.result.reason,'cancelled');self.assertEqual(result.result.usage['input_tokens'],2)
        self.assertEqual(len(self.provider.calls),1)
    def test_stale_summary_retains_charge_no_main(self):
        self.provider.hook=self.tx.clear
        result=self.agent().run('s','c','current',model=self.model)
        self.assertTrue(result.stale);self.assertEqual(result.result.usage['input_tokens'],2)
        self.assertEqual(len(self.provider.calls),1)
    def test_streaming_summary_and_main_real_stream_contract(self):
        self.provider=Streaming([START,delta('summary'),end(),STOP],[START,delta('answer'),end(),STOP])
        result=self.agent().run('s','c','current',model=replace(self.model,streaming=True))
        self.assertEqual(result.result.reason,'completed');self.assertEqual(self.provider.closed,2)
        self.assertEqual(result.result.usage['input_tokens'],6);self.assertEqual(result.result.usage['output_tokens'],4)
    def test_interrupted_stream_retains_observed_usage_without_replay(self):
        self.provider=Streaming([START,delta('partial'),RuntimeError('transport failed')])
        result=self.agent().run('s','c','current',model=replace(self.model,streaming=True))
        self.assertEqual(result.result.reason,'model_error')
        self.assertEqual(result.result.usage['input_tokens'],3)
        self.assertEqual(result.result.usage['output_tokens'],0)
        self.assertEqual(len(self.provider.calls),1);self.assertEqual(self.provider.closed,1)

    def test_stream_final_usage_charged_before_stale_fence(self):
        self.provider=Streaming([START,delta('summary'),end(),STOP])
        self.provider.hook=lambda event:self.tx.clear() if event['type']=='message_delta' else None
        result=self.agent().run('s','c','current',model=replace(self.model,streaming=True))
        self.assertTrue(result.stale)
        self.assertEqual(result.result.usage['input_tokens'],3)
        self.assertEqual(result.result.usage['output_tokens'],2)
        self.assertEqual(len(self.provider.calls),1)

    def test_lifecycle_rejects_late_charge_and_readonly_budget(self):
        contexts=[]
        def gate(c,a):contexts.append(c);return ALLOW(c,a)
        result=self.agent(gate=gate).run('s','c','current',model=self.model)
        before=deepcopy(result.result.usage)
        with self.assertRaises(RunStopped):contexts[0].charge_auxiliary_usage({'input_tokens':1})
        with self.assertRaises(AttributeError):contexts[0].remaining_tokens=99
        self.assertEqual(result.result.usage,before)
    def test_window_mismatch_rejected_before_admission(self):
        with self.assertRaises(ValueError):self.agent().run('s','c','current',model=MODEL)
        self.assertEqual(self.tx.history['s'],self.raw)
    def test_invalid_usage_never_main(self):
        m,stop,_=text('summary')
        for invalid in ({'input_tokens':True},{'input_tokens':-1},{'unknown':1}):
            self.provider=Provider([(m,stop,invalid)])
            result=self.agent().run('s',str(len(self.tx.turns)),'current',model=self.model)
            self.assertEqual(result.result.reason,'invalid_usage');self.assertEqual(len(self.provider.calls),1)

if __name__=='__main__':unittest.main()
