"""Inert gate-side persistence ambiguity; no provider/network/tool execution."""
import unittest
from artex.agent.runtime import BoundedAgent,EffectUncertain,Grant,RunStopped
import test_agent_runtime as fixture
class GateUncertaintyTests(unittest.TestCase):
    def test_model_gate_uncertainty_propagates_same_marker_before_provider(self):
        marker=EffectUncertain('synthetic admission receipt uncertainty');p=fixture.FakeProvider()
        def gate(*a):raise marker
        with self.assertRaises(EffectUncertain) as caught:BoundedAgent(p,gate=gate).run(fixture.request())
        self.assertIs(caught.exception,marker);self.assertEqual(p.requests,[])
    def test_tool_gate_uncertainty_never_executes_or_returns_terminal(self):
        marker=EffectUncertain('synthetic audit commit uncertainty');calls=[]
        p=fixture.FakeProvider(fixture.assistant('',[('call','echo',{})]))
        def gate(context,action):
            if action.kind=='tool':raise marker
            return Grant(True,True,True)
        with self.assertRaises(EffectUncertain) as caught:
            BoundedAgent(p,gate=gate,tools=[fixture.tool(lambda *a:calls.append(1) or 'ok')]).run(fixture.request())
        self.assertIs(caught.exception,marker);self.assertEqual(calls,[]);self.assertEqual(len(p.requests),1)
    def test_ordinary_gate_error_and_stopped_semantics_unchanged(self):
        for exception,reason in ((RuntimeError('private'),'gate_error'),(RunStopped('cancelled'),'cancelled')):
            def gate(*a):raise exception
            result=BoundedAgent(fixture.FakeProvider(),gate=gate).run(fixture.request())
            self.assertEqual(result.reason,reason)
