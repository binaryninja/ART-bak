"""Independent phase authority/accounting checks, no real providers or effects."""
import unittest
import test_runtime_settlement as fixtures
from test_agent_runtime import FakeProvider,assistant,request,grant
from artex.agent.runtime import RunStopped,Grant
from artex.manager import CancellationToken

class Review(unittest.TestCase):
    make=fixtures.SettlementTests.make
    first=fixtures.SettlementTests.first
    def test_cancel_during_settlement_model_gate_no_second_provider(self):
        token=CancellationToken();p=FakeProvider(self.first(),assistant())
        def gate(context,action):
            if action.kind=='model' and len(p.requests)==1:token.cancel()
            return grant(context,action)
        result=self.make(p,gate=gate).run(request(),token=token)
        self.assertEqual(len(p.requests),1);self.assertEqual(result.reason,'max_turns')
        self.assertEqual(result.events[-1]['reason'],'cancelled');self.assertFalse(result.events[-1]['completed'])
    def test_settlement_refusal_never_executes_requested_tool(self):
        calls=[];message,_,usage=assistant('', [('2','echo',{})])
        p=FakeProvider(self.first(),(message,'refusal',usage))
        result=self.make(p,callback=lambda *a:calls.append(1) or 'ok').run(request())
        self.assertEqual(calls,[1]);self.assertEqual(result.events[-1]['reason'],'refused')
        self.assertEqual(result.messages[-1]['content'][0]['tool_use_id'],'2')
        self.assertTrue(result.messages[-1]['content'][0]['is_error'])
    def test_auxiliary_usage_consumes_phase_budget_without_reset(self):
        p=FakeProvider(self.first(),assistant())
        def callback(context,args):context.charge_auxiliary_usage({'input_tokens':77});return 'ok'
        result=self.make(p,callback=callback).run(request())
        self.assertEqual(result.reason,'token_budget');self.assertEqual(result.usage['input_tokens'],79)
        self.assertEqual(result.usage['output_tokens'],2);self.assertEqual(len(p.requests),1)
        self.assertFalse(any(e['type']=='settlement_started' for e in result.events))
    def test_failure_after_delivered_model_work_never_starts_settlement(self):
        p=FakeProvider(self.first())
        def callback(context,args):raise RunStopped('mcp_outcome_uncertain')
        result=self.make(p,callback=callback).run(request())
        self.assertEqual(result.reason,'mcp_outcome_uncertain');self.assertEqual(len(p.requests),1)
        self.assertFalse(any(e['type']=='settlement_started' for e in result.events))
    def test_settlement_effect_budget_pairs_unexecuted_calls(self):
        calls=[]
        p=FakeProvider(self.first(),assistant('', [('2','echo',{}),('3','echo',{}),('4','echo',{})]))
        result=self.make(p,callback=lambda *a:calls.append(1) or 'ok').run(request())
        self.assertEqual(len(calls),3);self.assertEqual(result.events[-1]['reason'],'tool_budget')
        blocks=result.messages[-1]['content'];self.assertEqual([b['tool_use_id'] for b in blocks],['2','3','4'])
        self.assertEqual([b['is_error'] for b in blocks],[False,False,True])

if __name__=='__main__':unittest.main()
