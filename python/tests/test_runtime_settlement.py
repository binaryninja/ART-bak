import unittest
from artex.agent.runtime import BoundedAgent,Limits,SettlementBudget,RunStopped,Grant
from artex.agent.wrapup import SettlementPolicy
from artex.manager import CancellationToken
from test_agent_runtime import FakeProvider,assistant,request,tool,grant

class SettlementTests(unittest.TestCase):
    def make(self,p,*,callback=None,gate=grant,clock=lambda:0,disabled=(),tokens=20):
        return BoundedAgent(p,gate=gate,tools=[tool()] if callback is None else [tool(callback)],clock=clock,
            limits=Limits(max_turns=3,max_tokens=100,max_seconds=10,max_tool_calls=4),
            settlement=SettlementBudget(SettlementPolicy('Persist safely',disabled,2),2,tokens,2))
    def first(self):return assistant('', [('1','echo',{})])
    def test_original_reason_shared_ledger_pairing(self):
        p=FakeProvider(self.first(),assistant('wrapped'))
        r=self.make(p).run(request())
        self.assertEqual(r.reason,'max_turns');self.assertEqual(r.turns,2);self.assertEqual(r.tool_calls,1)
        self.assertEqual(r.usage['input_tokens'],4);self.assertEqual(p.requests[0]['max_tokens'],80)
        self.assertEqual(p.requests[1]['max_tokens'],20)
        self.assertEqual(p.requests[1]['messages'][-2]['content'][0]['tool_use_id'],'1')
        self.assertTrue(r.events[-1]['completed'])
    def test_disabled_tool_not_advertised_or_dispatched(self):
        calls=[];p=FakeProvider(self.first(),assistant('', [('2','echo',{})]))
        r=self.make(p,callback=lambda *a:calls.append(1) or 'ok',disabled=('echo',)).run(request())
        self.assertEqual(calls,[1]);self.assertEqual(p.requests[1]['tools'],[])
        self.assertEqual(r.events[-1]['reason'],'settlement_tool_disabled');self.assertFalse(r.events[-1]['completed'])
        self.assertEqual(r.messages[-1]['content'][0]['tool_use_id'],'2')
    def test_normal_completion_no_wrapup(self):
        p=FakeProvider(assistant());r=self.make(p).run(request());self.assertEqual(r.reason,'completed');self.assertEqual(len(p.requests),1)
    def test_failure_denial_cancel_refusal_do_not_enter(self):
        for mode in ('error','timeout','cancel','deny','refusal'):
            with self.subTest(mode=mode):
                token=CancellationToken();p=FakeProvider(self.first())
                def callback(*a):
                    if mode=='cancel':token.cancel()
                    if mode=='error':raise ValueError('uncertain')
                    if mode=='timeout':raise RunStopped('timeout')
                    return 'ok'
                def gate(c,a):return Grant() if mode=='deny' and a.kind=='tool' else grant(c,a)
                if mode=='refusal':p.responses=[({'role':'assistant','content':[]},'refusal',{})]
                r=self.make(p,callback=callback,gate=gate).run(request(),token=token)
                self.assertFalse(any(e['type']=='settlement_started' for e in r.events));self.assertEqual(len(p.requests),1)
    def test_clean_soft_timeout_vs_hard_timeout(self):
        for final,expected in ((8.5,2),(11,1)):
            now=[0];p=FakeProvider(self.first(),assistant())
            def callback(*a):now[0]=final;return 'ok'
            r=self.make(p,callback=callback,clock=lambda:now[0]).run(request())
            self.assertEqual(r.reason,'timeout');self.assertEqual(len(p.requests),expected)
    def test_turn_and_effect_caps_not_raised(self):
        p=FakeProvider(self.first(),assistant('', [('2','echo',{})]),assistant('', [('3','echo',{})]))
        r=self.make(p).run(request());self.assertEqual(r.turns,3);self.assertEqual(r.tool_calls,3)
        self.assertEqual(r.reason,'max_turns');self.assertFalse(r.events[-1]['completed'])
    def test_reserves_cannot_equal_host_ceiling(self):
        with self.assertRaises(ValueError):BoundedAgent(FakeProvider(),limits=Limits(max_turns=2),settlement=SettlementBudget(SettlementPolicy('safe',(),2),1,1,0))
    def test_default_remains_unchanged(self):
        p=FakeProvider(self.first());r=BoundedAgent(p,gate=grant,tools=[tool()],limits=Limits(max_turns=1)).run(request())
        self.assertEqual(r.reason,'max_turns');self.assertFalse(any('settlement' in e['type'] for e in r.events))
