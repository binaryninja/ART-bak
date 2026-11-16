"""Actual owned zero-task fixture, inert SQL/providers, no synthetic active flags."""
import unittest
import test_task_continuation as base
from artex.manager import TaskConflict,RuntimeUnavailable
class ContinuationHookTests(unittest.TestCase):
    def fixture(self):
        f=base.Tests();f.setUp();self.addCleanup(f.doCleanups);f.app.host.install_continuation(f.owner);return f
    def test_pause_resume_fresh_owner_without_goals_replay(self):
        f=self.fixture();old=f.handle;calls=list(f.f.p.calls)
        result=f.app.host.pause('5',timeout=0)
        self.assertTrue(result['paused']);self.assertTrue(old.token.cancelled)
        slot=f.app.manager._suspensions['5'];cap=slot['cap']
        self.assertTrue(f.app.manager.validate_suspension(cap,old,old.token,'paused'))
        self.assertFalse(f.app.runner._retire_cancelled('5',old))
        result=f.app.host.resume_suspended('5');self.assertFalse(result['paused'])
        new=f.app.runner.handles()[0]
        self.assertIsNot(new,old);self.assertIsNot(new.token,old.token);self.assertFalse(new.token.cancelled)
        self.assertTrue(f.app.manager.owns_generation('5',new.token,new));self.assertFalse(f.app.manager._suspensions)
        self.assertEqual(f.f.p.calls,calls);self.assertEqual(new.phase,'installed')
    def test_denied_pause_before_cancel_or_sql(self):
        f=self.fixture();f.allowed=False;calls=len(f.db.calls)
        with self.assertRaises(Exception):f.app.host.pause('5',timeout=0)
        self.assertFalse(f.handle.token.cancelled);self.assertFalse(f.app.manager._suspensions)
        self.assertEqual(len(f.db.calls),calls)
    def test_denied_resume_retains_both_generations(self):
        f=self.fixture();f.app.host.pause('5',timeout=0);f.allowed=False
        with self.assertRaises(RuntimeUnavailable):f.app.host.resume_suspended('5')
        self.assertEqual(f.app.manager._suspensions['5']['phase'],'quarantined')
        with self.assertRaises(TaskConflict):f.app.host.start('5')
        self.assertTrue(f.owner.retained(f.handle))
    def test_ordinary_start_cannot_bypass_exact_suspension(self):
        f=self.fixture();f.app.host.pause('5',timeout=0)
        with self.assertRaises(TaskConflict):f.app.host.start('5')
        self.assertFalse(f.app.manager.complete('5',f.handle.token))
        self.assertFalse(f.app.manager.validate_suspension(object(),f.handle,f.handle.token,'paused'))
    def test_clean_paused_slot_is_still_resource_ownership(self):
        f=self.fixture();f.app.host.pause('5',timeout=0)
        report=f.app.host.poll();self.assertEqual(report.continuation_owners,1);self.assertFalse(report.all_settled)
        self.assertFalse(f.app.host.drain(0).all_settled)
    def test_installation_tail_has_no_execution_grant_or_manager_lock_callback(self):
        f=self.fixture();f.app.host.pause('5',timeout=0);seen=[]
        original=f.db.fetch_all
        def inspect(query):
            slot=f.app.manager._suspensions.get('5')
            if slot is not None and slot['phase']=='installed':
                handle=slot['new_handle']
                seen.append(f.app.manager.owns_generation('5',handle.token,handle))
                self.assertFalse(f.app.manager._lock._is_owned())
                self.assertTrue(f.app.manager.validate_suspension(slot['cap'],f.handle,f.handle.token,'installed',new_handle=handle,new_token=handle.token))
            return original(query)
        f.db.fetch_all=inspect
        f.app.host.resume_suspended('5')
        self.assertTrue(seen);self.assertFalse(any(seen))
        self.assertTrue(f.app.manager.owns_generation('5',f.app.runner.handles()[0].token,f.app.runner.handles()[0]))
    def test_missing_facade_capacity_grant_denies_before_resume_write(self):
        from artex.server.task_host import TaskHTTPManagerFacade
        f=self.fixture();f.app.host.pause('5',timeout=0);before=len(f.db.calls)
        facade=TaskHTTPManagerFacade(f.app.host)
        with self.assertRaises(RuntimeUnavailable):facade.control_task('5','resume')
        self.assertEqual(len(f.db.calls),before);self.assertEqual(f.app.manager._suspensions['5']['phase'],'paused')
    def test_queued_row_cannot_use_direct_resume_or_ordinary_start(self):
        f=self.fixture();f.app.host.pause('5',timeout=0);f.app.manager._tasks['5']['queued']=True
        with self.assertRaises(TaskConflict):f.app.host.resume_suspended('5')
        with self.assertRaises(TaskConflict):f.app.host.start('5')
    def test_replaced_owner_method_not_executed_during_resume(self):
        f=self.fixture();f.app.host.pause('5',timeout=0);called=[];before=len(f.db.calls)
        f.owner.prepare_resume=lambda *a:called.append(True)
        with self.assertRaises(RuntimeUnavailable):f.app.host.resume_suspended('5')
        self.assertFalse(called);self.assertEqual(len(f.db.calls),before)
        self.assertEqual(f.app.manager._suspensions['5']['phase'],'quarantined')
