"""Independent runtime duration checks with inert clocks; no transport IO."""
import unittest
from artex.agent.runtime import RuntimeContext,RunStopped
from artex.manager import CancellationToken

class ClockReview(unittest.TestCase):
    def test_backwards_samples_inside_one_remaining_call_rejected(self):
        values=iter((9.0,8.0))
        context=RuntimeContext({},CancellationToken(),20.0,lambda:next(values))
        with self.assertRaises(RunStopped):context.remaining_seconds()
    def test_clock_cancelling_token_cannot_return_usable_duration(self):
        token=CancellationToken()
        def clock():
            token.cancel('fixture')
            return 1.0
        context=RuntimeContext({},token,20.0,clock)
        with self.assertRaises(RunStopped):context.remaining_seconds()
    def test_shortening_is_monotonic_and_foreign_clock_rejected(self):
        clock=lambda:1.0
        context=RuntimeContext({},CancellationToken(),20.0,clock)
        self.assertEqual(context.clamp_deadline(5.0,clock=clock),4.0)
        self.assertEqual(context.clamp_deadline(10.0,clock=clock),4.0)
        with self.assertRaises(RunStopped):context.clamp_deadline(3.0,clock=lambda:1.0)
    def test_clock_shortening_deadline_returns_current_not_prefetched_ceiling(self):
        context=None
        def clock():
            context.deadline=5.0
            return 1.0
        context=RuntimeContext({},CancellationToken(),20.0,clock)
        self.assertEqual(context.remaining_seconds(),4.0)
