"""Host lifecycle races using only inert model/repository fixtures."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest

from artex.agent.host import AgentHost
from artex.manager import TaskConflict, TaskManager, RuntimeUnavailable
from test_agent_runner import make_runner, Model
from test_manager import Repository, record, enrich


def host_for(runner, repository=None):
    manager=TaskManager(repository or Repository(),runner=runner,dto_enricher=enrich)
    manager.load_existing()
    return AgentHost(manager,runner)


class HostTests(unittest.TestCase):
    def test_fast_completion_explicit_poll_not_admission(self):
        host=host_for(make_runner())
        self.assertTrue(host.start('1'))
        self.assertTrue(host.runner.handles()[0].wait(2))
        self.assertEqual(host.manager.status('1'),'running')
        state=host.poll()
        self.assertEqual(state.settled,('1',))
        self.assertTrue(state.all_settled)
        self.assertEqual(host.manager.status('1'),'done')
        self.assertEqual(host.poll().settled,())

    def test_poll_busy_while_start_has_not_installed_handle(self):
        runner=make_runner(); host=host_for(runner)
        real_start=runner.start; returning,release=Event(),Event()
        def delayed_start(task, token):
            handle=real_start(task,token)
            self.assertTrue(handle.wait(2))
            returning.set()
            if not release.wait(3): raise RuntimeError('test gate timed out')
            return handle
        runner.start=delayed_start
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(host.start,'1')
            try:
                self.assertTrue(returning.wait(2))
                self.assertTrue(host.poll().busy)
                self.assertFalse(host.drain(0).workers_drained)
                self.assertNotEqual(host.manager.lifecycle_snapshot('1')['status'],'done')
            finally: release.set()
            self.assertTrue(future.result(2))
        self.assertTrue(host.poll().all_settled)
        self.assertEqual(host.manager.status('1'),'done')

    def test_pause_during_start_is_not_blocked_by_host_fence(self):
        runner=make_runner(); host=host_for(runner)
        real_start=runner.start; returning,release=Event(),Event()
        def delayed_start(task,token):
            handle=real_start(task,token); returning.set()
            if not release.wait(3): raise RuntimeError('test gate timed out')
            return handle
        runner.start=delayed_start
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(host.start,'1')
            try:
                self.assertTrue(returning.wait(2))
                with self.assertRaises(TaskConflict): host.pause('1',timeout=0)
                self.assertTrue(host.poll().busy)
            finally: release.set()
            self.assertFalse(future.result(2))
        self.assertTrue(host.runner.handles()[0].wait(2))
        self.assertTrue(host.pause('1',timeout=1)['paused'])
        self.assertTrue(host.poll().all_settled)
        self.assertNotEqual(host.manager.lifecycle_snapshot('1')['status'],'done')

    def test_drain_waits_and_temporarily_rejects_admission(self):
        entered,release=Event(),Event(); runner=make_runner(Model(entered,release))
        host=host_for(runner,Repository(record(1),record(2))); host.start('1')
        self.assertTrue(entered.wait(2)); handle=runner.handles()[0]
        waiting=Event(); real_wait=handle.wait
        def observed_wait(timeout=None):
            if timeout and timeout > 0: waiting.set()
            return real_wait(timeout)
        handle.wait=observed_wait
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(host.drain,2)
            try:
                self.assertTrue(waiting.wait(1))
                with self.assertRaises(TaskConflict): host.start('2')
            finally: release.set()
            outcome=future.result(2)
        self.assertTrue(outcome.workers_drained)
        self.assertTrue(outcome.all_settled)
        self.assertEqual(host.manager.status('1'),'done')
        self.assertTrue(host.start('2'))
        self.assertTrue(runner.handles()[0].wait(2)); host.poll()

    def test_timed_out_drain_does_not_cancel_or_mark_paused(self):
        entered,release=Event(),Event(); host=host_for(make_runner(Model(entered,release)))
        host.start('1'); handle=host.runner.handles()[0]
        try:
            self.assertTrue(entered.wait(2))
            outcome=host.drain(0)
            self.assertFalse(outcome.workers_drained)
            self.assertFalse(handle.token.cancelled)
            self.assertFalse(host.manager.lifecycle_snapshot('1')['paused'])
            self.assertEqual(outcome.settlement.running,1)
        finally: release.set(); self.assertTrue(handle.wait(2)); host.poll()

    def test_old_generation_cannot_settle_new_resume(self):
        entered,release=Event(),Event(); host=host_for(make_runner(Model(entered,release)))
        host.start('1'); old=host.runner.handles()[0]
        self.assertTrue(entered.wait(2)); old.cancel(); release.set(); self.assertTrue(old.wait(2))
        host.pause('1',timeout=1)
        self.assertTrue(host.poll().all_settled)
        result=host.control_task('1','resume')
        self.assertEqual(result['status'],'running')
        newer=host.runner.handles()[0]; self.assertTrue(newer.wait(2))
        self.assertFalse(host.manager.complete('1',old.token,'done'))
        self.assertTrue(host.poll().all_settled)
        self.assertEqual(host.manager.status('1'),'done')

    def test_failed_settlement_does_not_turn_successful_start_into_retry(self):
        host=host_for(make_runner()); self.assertTrue(host.start('1'))
        handle=host.runner.handles()[0]; self.assertTrue(handle.wait(2))
        real_complete=host.manager.complete
        host.manager.complete=lambda *args: (_ for _ in ()).throw(RuntimeError('fixture storage failure'))
        with self.assertRaises(RuntimeError): host.poll()
        self.assertEqual(host.runner.handles(),(handle,))
        self.assertEqual(host.manager.status('1'),'running')
        host.manager.complete=real_complete
        self.assertTrue(host.poll().all_settled)

    def test_refused_settlement_keeps_pending_without_claiming_done(self):
        host=host_for(make_runner()); host.start('1'); self.assertTrue(host.runner.handles()[0].wait(2))
        real=host.manager.complete; host.manager.complete=lambda *args:False
        state=host.poll()
        self.assertEqual((state.running,state.pending),(0,1))
        self.assertFalse(state.all_settled)
        outcome=host.drain(0)
        self.assertTrue(outcome.workers_drained); self.assertFalse(outcome.all_settled)
        host.manager.complete=real; host.poll()

    def test_create_uses_admission_fence_without_implicit_completion(self):
        host=host_for(make_runner())
        task=host.create_task('inert fixture','inert goal')
        self.assertEqual(task['status'],'running')
        self.assertTrue(host.runner.handles()[0].wait(2))
        self.assertEqual(host.poll().settled,(task['id'],))
        self.assertEqual(host.manager.status(task['id']),'done')

    def test_failed_admission_does_not_leave_busy_fence(self):
        host=host_for(make_runner(readiness=lambda task:False))
        with self.assertRaises(RuntimeUnavailable): host.start('1')
        self.assertFalse(host.poll().busy)
        self.assertEqual(host.runner.handles(),())

    def test_config_and_reentrant_settlement_fail_closed(self):
        runner=make_runner(); other=make_runner(); manager=TaskManager(Repository(),runner=runner)
        with self.assertRaises(RuntimeUnavailable): AgentHost(manager,other)
        host=host_for(runner); host.start('1'); self.assertTrue(runner.handles()[0].wait(2))
        real=host.manager.complete
        host.manager.complete=lambda *args:host.start('1')
        with self.assertRaises(TaskConflict): host.poll()
        host.manager.complete=real; host.poll()
        for timeout in (-1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError): host.drain(timeout)


if __name__=='__main__':unittest.main()
