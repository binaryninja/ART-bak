"""Actual host/outer/worker gate uncertainty, no live tools or provider effects."""
import unittest
from artex.agent.bootstrap import BootstrapUnresolved
from artex.agent.runtime import EffectUncertain
import test_inflight_cleanup as fixture

class GateEngineReview(unittest.TestCase):
    def test_gate_uncertainty_retains_canonical_claim_and_cleanup_no_replay(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);f.f.f.seed()
        worker=f.h._child.worker;calls=[];failure=EffectUncertain('inert gate audit acknowledgement unknown')
        def gate(context,action):calls.append(action);raise failure
        worker.authorize=gate
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertEqual(len(calls),1);self.assertEqual(f.f.f.wp.calls,[])
        self.assertEqual(worker.store.get(3).node.state,'running')
        self.assertTrue(f.h.unresolved);self.assertTrue(f.f.manager._has_cleanup('5'))
        owner=f.h._child.cleanup;records=list(owner._records.values());self.assertEqual(len(records),1)
        record=records[0]
        self.assertIsNone(owner.transcript.finalized_scoped_result(record['session'],record['turn'].id,record['identity']))
        self.assertFalse(f.f.host.drain(0).all_settled)
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,2)
        self.assertEqual(len(calls),1);self.assertEqual(f.f.f.wp.calls,[])
