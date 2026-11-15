"""Independent scheduler integration regressions; inert callbacks only."""
import unittest
from artex.scheduler import FIFOScheduler, LocalHostAdapter
from test_scheduler import Adapter, task
from test_agent_host import host_for
from test_agent_runner import make_runner
from test_manager import Repository, record, NOW


class SchedulerReviewTests(unittest.TestCase):
    def test_unavailable_local_runner_is_deferred_not_tick_abort(self):
        runner = make_runner(readiness=lambda task:False)
        host = host_for(runner, Repository(record(queued=True,queued_at=NOW,queue_mode='resume')))
        adapter = LocalHostAdapter(host,single_process=True)
        scheduler = FIFOScheduler(adapter,adapter,clock=lambda:NOW.timestamp())
        result = scheduler.tick()
        self.assertEqual(result.deferred,(1,))
        self.assertEqual(result.admitted,())
        self.assertEqual(runner.handles(),())

    def test_deferred_but_owned_work_still_reserves_capacity(self):
        class Retained(Adapter):
            def admit_queued(self,row):
                if row.task_id == 1:
                    self.running = 1  # Startup cancellation: handle not drained.
                    return 'deferred'
                return super().admit_queued(row)
        adapter = Retained([task(1),task(2)])
        scheduler = FIFOScheduler(adapter,adapter,clock=lambda:100,limit=1)
        result = scheduler.tick()
        self.assertEqual(result.admitted,())
        self.assertEqual(result.deferred,(1,))
        self.assertEqual(adapter.running,1)
