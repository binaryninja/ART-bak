"""Independent wrapper ownership checks with inert authored fixture."""
import unittest
import test_task_runtime_application as fixture
from artex.manager import RuntimeUnavailable
class Review(unittest.TestCase):
    def make(self):
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_foreign_executor_rejected_before_startup_reads(self):
        f=self.make();before=list(f.f.db.calls);f.runtime.executor=object()
        with self.assertRaises((ValueError,RuntimeUnavailable)):f.build()
        self.assertEqual(f.f.db.calls,before);self.assertEqual(f.p.calls,[])
    def test_rebound_pump_cannot_step_or_start(self):
        f=self.make();b=f.build();b.pump.runner=object()
        # Cleanup stops original pump directly because wrapper now fails closed.
        f._cleanups.pop()
        self.addCleanup(lambda:b.pump.stop(1))
        for op in (b.tick,b.start_pump):
            with self.assertRaises(RuntimeUnavailable):op()
        self.assertEqual(f.p.calls,[]);self.assertIsNone(b.pump._thread)
