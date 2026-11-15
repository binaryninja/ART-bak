"""Real EngineRunner/AgentHost and persisted TaskStore with inert SQL/engine fixtures."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from artex.agent.admission import AdmissionController
from artex.agent.host import AgentHost
from artex.agent.engine_runner import EngineRunner
from artex.agent.engine import EngineScope
from artex.db.tasks import TaskStore
from artex.manager import TaskManager,RuntimeUnavailable
from artex.scheduler import FIFOScheduler,_snapshot
from artex.server.task_host import TaskHTTPManagerFacade
from test_hosted_setup import DB
import test_engine
class Tests(unittest.TestCase):
    def setUp(self):
        self.f=test_engine.EngineTests();self.f.database_factory=DB;self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.calls=[]
        def factory(row,token,scope):self.calls.append(row);self.f.token=token;return self.f.make()
        self.runner=EngineRunner(readiness=lambda row:EngineScope(5,9,1,'r1'),factory=factory)
        self.manager=TaskManager(TaskStore(self.f.db),runner=self.runner);self.manager.load_existing()
        self.host=AgentHost(self.manager,self.runner)
        self.capacity=AdmissionController(self.host,limit=1,single_process=True)
        self.facade=TaskHTTPManagerFacade(self.host,admission_guard=self.capacity.admission_guard)
    def test_constructor_inert_and_one_authority(self):
        self.assertFalse(self.calls);self.assertFalse(self.runner.handles())
        with self.assertRaises(RuntimeUnavailable):AdmissionController(self.host,single_process=True)
        with self.assertRaises(RuntimeUnavailable):AdmissionController(self.host)
    def test_direct_uses_real_pending_ownership_not_running_flag(self):
        self.facade.control_task('5','resume');self.assertEqual(len(self.calls),1)
        state=self.capacity.poll();self.assertEqual((state.running,state.pending),(0,1))
        with self.assertRaises(RuntimeUnavailable):self.facade.start('5')
        self.assertEqual(len(self.calls),1)
        self.facade.control_task('5','pause');self.assertEqual(self.capacity.poll().pending,0)
        self.facade.control_task('5','resume');self.assertEqual(len(self.calls),2)
    def test_unresolved_cancelled_handle_still_counts(self):
        self.facade.control_task('5','resume');h=self.runner.handles()[0];h._unresolved=True;h.cancel()
        state=self.capacity.poll();self.assertEqual(state.running+state.pending,1)
        with self.assertRaises(RuntimeUnavailable):self.facade.start('5')
        self.assertEqual(len(self.calls),1)
    def test_queue_and_direct_share_real_gate(self):
        row=self.manager._tasks['5'];row.update(paused=False,queued=True,queue_mode='resume')
        scheduler=FIFOScheduler(self.capacity,self.capacity,clock=lambda:0,limit=1)
        result=scheduler.tick();self.assertEqual(result.admitted,(5,));self.assertEqual(len(self.calls),1)
        with self.assertRaises(RuntimeUnavailable):self.facade.start('5')
        self.assertEqual(scheduler.tick().admitted,())
    def test_failure_without_live_work_reconciles_and_releases(self):
        original=self.runner._factory
        def fail(*a):raise RuntimeError('inert failure')
        self.runner._factory=fail
        with self.assertRaises(RuntimeError):self.facade.control_task('5','resume')
        self.assertFalse(self.runner.handles());self.assertFalse(self.capacity.poll().busy)
        self.runner._factory=original
        self.facade.control_task('5','resume');self.assertEqual(len(self.calls),1)
    def test_poll_failure_quarantines_until_explicit_reconcile(self):
        original=self.host.poll
        def broken():raise RuntimeError('no inventory')
        self.host.poll=broken
        with self.assertRaises(RuntimeError):
            with self.capacity.admission_guard('start','5'):self.fail('must not enter')
        self.host.poll=original
        self.assertTrue(self.capacity.poll().busy)
        with self.assertRaises(RuntimeUnavailable):self.facade.start('5')
        self.assertFalse(self.capacity.reconcile().busy)
        self.facade.control_task('5','resume')
    def test_reentrant_poll_drain_admission_fail_fast(self):
        with self.capacity.admission_guard('start','5'):
            self.assertTrue(self.capacity.poll().busy)
            with self.assertRaises(RuntimeUnavailable):self.capacity.drain(0)
            with self.assertRaises(RuntimeUnavailable):
                with self.capacity.admission_guard('start','5'):self.fail('reentered')
        self.assertFalse(self.capacity.poll().busy)
    def test_direct_reservation_blocks_queued_and_second_direct(self):
        entered,release=Event(),Event();original=self.runner._factory
        def delayed(*args):
            entered.set()
            if not release.wait(3):raise RuntimeError('test timeout')
            return original(*args)
        self.runner._factory=delayed
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.facade.control_task,'5','resume')
            try:
                self.assertTrue(entered.wait(2));self.assertTrue(self.capacity.poll().busy)
                with self.assertRaises(RuntimeUnavailable):self.facade.start('5')
                self.assertEqual(self.capacity.admit_queued(_snapshot(self.manager.lifecycle_snapshot('5'))),'deferred')
            finally:release.set()
            future.result(2)
        self.assertEqual(len(self.calls),1)
    def test_final_reconciliation_failure_no_false_capacity_release(self):
        original=self.host.poll;calls=[]
        def flaky():
            calls.append(1)
            if len(calls)==2:raise RuntimeError('post operation unavailable')
            return original()
        self.host.poll=flaky
        self.facade.control_task('5','resume');self.assertTrue(self.capacity.poll().busy)
        self.assertEqual(len(self.runner.handles()),1)
        self.host.poll=original;self.assertEqual(self.capacity.reconcile().pending,1)
        with self.assertRaises(RuntimeUnavailable):self.facade.start('5')

class BootstrapCapacityTests(unittest.TestCase):
    def test_installed_unadvanced_bootstrap_reserves_capacity(self):
        import test_engine_runner_bootstrap as fixture
        f=fixture.EngineBootstrapTests();f.setUp();self.addCleanup(f.doCleanups)
        authority=AdmissionController(f.f.host,limit=1,single_process=True)
        facade=TaskHTTPManagerFacade(f.f.host,admission_guard=authority.admission_guard)
        facade.start('5');handle=f.runner.handles()[0]
        self.assertIsNone(handle.scope);self.assertIsNone(f.f.db.receipt)
        self.assertEqual(authority.poll().pending,1)
        with self.assertRaises(RuntimeUnavailable):facade.start('5')
        self.assertEqual(f.f.provider.calls,[])
        facade.control_task('5','pause')
        self.assertEqual(authority.poll().pending,0)
