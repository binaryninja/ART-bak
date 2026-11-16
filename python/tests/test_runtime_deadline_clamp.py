import unittest
from artex.agent.runtime import RuntimeContext,RunStopped
class Token:cancelled=False
class Tests(unittest.TestCase):
    def setUp(self):
        self.now=100.;self.clock=lambda:self.now;self.token=Token()
        self.context=RuntimeContext({},self.token,110.,self.clock)
    def test_shorten_same_context_then_never_extend(self):
        self.assertEqual(self.context.clamp_deadline(104.,clock=self.clock),4.)
        self.assertEqual(self.context.deadline,104.)
        self.assertEqual(self.context.clamp_deadline(999.,clock=self.clock),4.)
        self.now=103.;self.assertEqual(self.context.remaining_seconds(),1.)
        self.now=104.
        with self.assertRaises(RunStopped):self.context.remaining_seconds()
    def test_different_clock_origin_rejected_without_mutation(self):
        with self.assertRaises(RunStopped):self.context.clamp_deadline(9999.,clock=lambda:9990.)
        self.assertEqual(self.context.deadline,110.)
    def test_invalid_candidate_and_rebind_fail(self):
        for value in (True,float('inf'),float('nan')):
            with self.assertRaises(RunStopped):self.context.clamp_deadline(value,clock=self.clock)
        self.context.clock=lambda:100.
        with self.assertRaises(RunStopped):self.context.remaining_seconds()
    def test_backwards_clock_and_manual_widening_refused(self):
        self.context.remaining_seconds();self.now=99.
        with self.assertRaises(RunStopped):self.context.remaining_seconds()
        self.now=100.;self.context.deadline=111.
        with self.assertRaises(RunStopped):self.context.remaining_seconds()
    def test_cancelled_or_expired_never_zero_unlimited(self):
        self.token.cancelled=True
        with self.assertRaises(RunStopped):self.context.remaining_seconds()
        self.token.cancelled=False
        with self.assertRaises(RunStopped):self.context.clamp_deadline(99.,clock=self.clock)
        self.assertEqual(self.context.deadline,99.)
