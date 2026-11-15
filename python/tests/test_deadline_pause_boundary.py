"""Current fail-closed lease boundary; synthetic real manager/host, no timeout enablement."""
import unittest
from artex.db.task_deadline import DeadlineConflict
from artex.db.tasks import TaskCreateOptions
from artex.manager import RuntimeUnavailable
import test_task_deadline as fixture


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_drained_pause_revokes_old_clock_owner_without_reauthorizing_it(self):
        h=self.f.handle;host=self.f.f.f.host;manager=self.f.f.f.manager
        self.assertTrue(manager.owns_generation(h.task_id,h.token,h))
        host.pause(h.task_id)
        self.assertTrue(h.wait(0));self.assertFalse(manager.owns_generation(h.task_id,h.token,h,allow_cancelled=True))
        self.f.sql.clear()
        with self.assertRaises(DeadlineConflict):self.f.store.validate_current(0)
        self.assertEqual(self.f.sql,[])
        self.assertEqual(host.poll().settled,())
        self.assertEqual(self.f.f.runner.handles(),())
    def test_positive_deadline_rejected_at_runner_preflight_before_persistence(self):
        self.f.sql.clear()
        with self.assertRaises(RuntimeUnavailable):
            self.f.f.runner.validate_create('synthetic','goal',TaskCreateOptions(timeout_seconds=30))
        self.assertEqual(self.f.sql,[])
