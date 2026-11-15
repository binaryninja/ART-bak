import unittest
from threading import Thread,Event
from artex.agent.pump import EnginePump
from artex.manager import RuntimeUnavailable,TaskConflict
import test_engine_runner_review as fixture

class PumpTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.EngineRunnerReview();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.pump=EnginePump(self.f.host,interval=.01,clock=lambda:0)
        self.addCleanup(lambda:self.pump.stop(2))
    def start_task(self):self.f.host.start('5');return self.f.runner.handles()[0]
    def test_constructor_inert_explicit_tick_real_children(self):
        self.assertIsNone(self.pump._thread);self.assertEqual(self.f.runner.handles(),())
        self.start_task();self.assertEqual(self.pump.tick().advanced,('5',))
        self.assertEqual(self.pump.tick().advanced,('5',))
        self.assertEqual(self.f.manager.status('5'),'running')
        self.f.f.met();self.assertEqual(self.pump.tick().settlement.settled,('5',))
    def test_idle_stop_does_not_cancel_or_complete(self):
        handle=self.start_task();self.assertTrue(self.pump.stop(0))
        self.assertFalse(handle.token.cancelled);self.assertEqual(self.f.manager.status('5'),'running')
        self.assertTrue(self.pump.tick().stopped)
    def test_blocked_actual_provider_stop_timeout_keeps_owned(self):
        handle=self.start_task();entered=Event();release=Event();provider=self.f.f.pp;original=provider.complete
        def blocked(*a):entered.set();release.wait(2);return original(*a)
        provider.complete=blocked;self.addCleanup(release.set)
        self.assertTrue(self.pump.start());self.assertTrue(entered.wait(1))
        self.assertTrue(self.pump.tick().busy);self.assertFalse(self.pump.stop(.01))
        self.assertFalse(handle.token.cancelled);self.assertTrue(self.pump._thread.is_alive())
        self.assertFalse(self.pump.start()) # same owned thread, not another loop
        release.set();self.assertTrue(self.pump.stop(2));self.assertFalse(self.pump._thread.is_alive())
    def test_paused_handle_never_advances(self):
        handle=self.start_task();self.f.host.pause('5')
        self.assertEqual(self.pump.tick().advanced,());self.assertTrue(handle.token.cancelled)
    def test_uncertainty_never_retries(self):
        handle=self.start_task();calls=[]
        def fail(*a):calls.append(1);raise RuntimeError('private provider failure')
        self.f.runner.advance=fail
        with self.assertRaises(RuntimeError):self.pump.tick()
        self.assertTrue(self.pump.tick().stopped);self.assertEqual(calls,[1]);self.assertEqual(self.pump.failure,'engine_pump_unresolved')
        with self.assertRaises(RuntimeUnavailable):self.pump.start()
        self.assertIn(handle,self.f.runner.handles())
    def test_one_pump_per_host(self):
        with self.assertRaises(RuntimeUnavailable):EnginePump(self.f.host)
    def test_unresolved_handle_not_advanced_or_discarded(self):
        h=self.start_task();h._unresolved=True
        self.assertEqual(self.pump.tick().advanced,());self.assertIn(h,self.f.runner.handles())
    def test_tick_during_early_handle_installation_busy(self):
        entered=Event();release=Event();original=self.f.runner.start
        def blocked(*a):
            h=original(*a);entered.set();release.wait(2);return h
        self.f.runner.start=blocked
        t=Thread(target=lambda:self.f.host.start('5'));t.start();self.addCleanup(release.set);self.addCleanup(lambda:t.join(2))
        self.assertTrue(entered.wait(1));self.assertTrue(self.pump.tick().busy)
        self.assertEqual(self.f.f.pp.calls,[]);release.set();t.join(2)
    def test_drain_tick_boundary_and_pause_responsiveness(self):
        h=self.start_task();entered=Event();release=Event();original=h.advance
        def blocked(now):entered.set();release.wait(2);return original(now)
        h.advance=blocked;t=Thread(target=self.pump.tick);t.start();self.addCleanup(release.set);self.addCleanup(lambda:t.join(2))
        self.assertTrue(entered.wait(1));self.assertTrue(self.f.host.poll().busy)
        self.assertFalse(self.f.host.drain(.01).workers_drained)
        # Cancellation is explicit through normal lifecycle, never pump.stop.
        result=self.f.host.pause('5',timeout=0)
        self.assertTrue(h.token.cancelled)
        release.set();t.join(2);self.assertFalse(t.is_alive())
    def test_owned_work_prevents_nested_admission_and_releases_on_error(self):
        def work():
            with self.assertRaises(TaskConflict):self.f.host.start('5')
            raise RuntimeError('fixture')
        with self.assertRaises(RuntimeError):self.f.host.advance_owned(work)
        self.assertFalse(self.f.host.poll().busy);self.assertTrue(self.f.host.start('5'))
    def test_start_during_stop_join_cannot_spawn_replacement(self):
        self.pump.start();thread=self.pump._thread;entered=Event();release=Event();original=thread.join
        def blocked_join(timeout=None):entered.set();release.wait(2);return original(timeout)
        thread.join=blocked_join
        results=[];stopper=Thread(target=lambda:results.append(self.pump.stop(2)))
        stopper.start();self.addCleanup(release.set);self.addCleanup(lambda:stopper.join(2))
        self.assertTrue(entered.wait(1))
        with self.assertRaises(RuntimeUnavailable):self.pump.start()
        self.assertIs(self.pump._thread,thread)
        release.set();stopper.join(2);self.assertEqual(results,[True])
