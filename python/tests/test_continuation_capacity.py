"""Exact replacement-credit negatives over the composed inert JWT runtime.

Database is synthetic here; genuine PostgreSQL acceptance is recorded separately.
"""
import unittest
from copy import copy
from dataclasses import replace
from threading import Thread
from unittest.mock import patch
from artex.manager import TaskConflict,RuntimeUnavailable,_ContinuationCredit
from artex.agent.host import PollResult
import test_task_continuation_setup as setup

class Tests(unittest.TestCase):
    def make(self):
        f=setup.Tests();f.setUp();self.addCleanup(f.doCleanups);f.capacity=1;f.prepare()
        self.assertEqual(f.f.request('/api/tasks/5/control',{'action':'pause'})[0],200)
        return f
    def resume(self,f):return f.f.request('/api/tasks/5/control',{'action':'resume'})
    def test_capacity_one_exact_replacement_has_one_handle_and_no_goal_replay(self):
        f=self.make();calls=list(f.f.p.calls);old=f.handle
        self.assertEqual(self.resume(f)[0],200)
        new,=f.app.runner.handles();self.assertIsNot(new,old);self.assertIsNot(new.token,old.token)
        self.assertTrue(old.token.cancelled);self.assertFalse(new.token.cancelled)
        self.assertFalse(f.app.manager._continuation_credits)
        self.assertEqual(f.f.p.calls,calls);f.app.tick()
        self.assertIs(new._receipt,old._receipt);self.assertEqual(f.f.p.calls,calls)
        self.assertEqual(f.app.admission.poll().pending,1)
    def test_other_occupancy_is_not_credited(self):
        # Exercise arithmetic without inventing a second admitted generation in
        # this deliberately single-row DB fixture. Actual two-task DB QA separate.
        f=self.make();a=f.app.admission;m=f.app.manager;original=a._poll_owned
        def inventory():
            state=original();return replace(state,pending=state.pending+1)
        before=len(f.grants)
        with patch.object(a,'_poll_owned',side_effect=inventory):
            self.assertEqual(self.resume(f)[0],503)
        self.assertEqual(len(f.grants),before);self.assertFalse(m._continuation_credits)
        self.assertEqual(m._suspensions['5']['phase'],'paused')
    def test_wrong_task_cannot_borrow_paused_capacity(self):
        f=self.make();m=f.app.manager
        with self.assertRaises(RuntimeUnavailable):
            with f.app.admission.admission_guard('resume','6'):self.fail('wrong task admitted')
        self.assertFalse(m._continuation_credits);self.assertEqual(m._suspensions['5']['phase'],'paused')
    def test_copied_forged_stale_credit_rejected_and_original_released(self):
        f=self.make();m=f.app.manager
        with f.app.admission.admission_guard('resume','5'):
            c=m._suspensions['5']['credit'];self.assertTrue(m.validate_continuation_admission(c))
            forged=_ContinuationCredit(c.controller,c.claim,c.thread,c.task_id,c.cap,c.handle,c.token)
            for invalid in (copy(c),forged):
                self.assertFalse(m.validate_continuation_admission(invalid))
                with self.assertRaises(TaskConflict):m.release_continuation_admission(invalid)
        self.assertFalse(m.validate_continuation_admission(c))
        with self.assertRaises(TaskConflict):m.release_continuation_admission(c)
        self.assertNotIn('credit',m._suspensions['5']);self.assertFalse(m._continuation_credits)
    def test_foreign_thread_and_nested_admission_cannot_consume_credit(self):
        f=self.make();m=f.app.manager;errors=[]
        with f.app.admission.admission_guard('resume','5'):
            def foreign():
                try:m.resume_suspended('5')
                except Exception as e:errors.append(type(e))
            t=Thread(target=foreign);t.start();t.join(2);self.assertFalse(t.is_alive())
            self.assertEqual(errors,[TaskConflict]);self.assertEqual(m._suspensions['5']['phase'],'paused')
            with self.assertRaises(RuntimeUnavailable):
                with f.app.admission.admission_guard('resume','5'):self.fail('nested admission')
        self.assertFalse(m._continuation_credits)
    def test_replaced_runner_entry_cannot_claim_credit(self):
        f=self.make();m=f.app.manager
        old=f.app.runner._handles.pop('5')
        try:self.assertEqual(self.resume(f)[0],409)
        finally:f.app.runner._handles['5']=old
        self.assertFalse(m._continuation_credits);self.assertNotIn('credit',m._suspensions['5'])
    def test_revoked_configuration_does_not_write_resume(self):
        f=self.make();m=f.app.manager
        f.f.p.rows[2]=replace(f.f.p.rows[2],model='revoked configuration')
        queries=[];original=f.f.f.db.fetch_all
        def observed(q):queries.append(q.sql);return original(q)
        f.f.f.db.fetch_all=observed
        self.assertNotEqual(self.resume(f)[0],200)
        self.assertFalse(any(q.startswith('UPDATE tasks SET paused=false') for q in queries))
        self.assertFalse(m._continuation_credits);self.assertTrue(f.enabled.owner.retained(f.handle))
    def test_post_cas_response_loss_retains_generations_and_blocks_retry(self):
        f=self.make();db=f.f.f.db;original=db.fetch_all;writes=[]
        def lost(q):
            result=original(q)
            if q.sql.startswith('UPDATE tasks SET paused=false'):
                writes.append(q);raise RuntimeError('inert post-CAS response loss')
            return result
        db.fetch_all=lost
        self.assertNotEqual(self.resume(f)[0],200);self.assertEqual(len(writes),1)
        self.assert_retained_failure(f)
        self.assertNotEqual(self.resume(f)[0],200);self.assertEqual(len(writes),1)
    def test_install_failure_retains_generations_and_releases_transient_credit(self):
        f=self.make()
        with patch.object(f.app.runner,'install_resumed_handle',side_effect=RuntimeError('inert install failure')):
            self.assertNotEqual(self.resume(f)[0],200)
        self.assert_retained_failure(f)
        before=list(f.f.p.calls);self.assertNotEqual(self.resume(f)[0],200);self.assertEqual(f.f.p.calls,before)
    def test_credit_release_error_does_not_mask_original_failure(self):
        f=self.make();m=f.app.manager;original=m.release_continuation_admission
        def broken(c):
            original(c)
            raise TaskConflict('inert release acknowledgment loss')
        with patch.object(m,'release_continuation_admission',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'original operation error'):
                with f.app.admission.admission_guard('resume','5'):
                    raise RuntimeError('original operation error')
        self.assertFalse(m._continuation_credits)
        self.assertTrue(f.app.admission._quarantined)
        self.assertFalse(f.app.admission._busy)

    def assert_retained_failure(self,f):
        m=f.app.manager;slot=m._suspensions['5']
        self.assertEqual(slot['phase'],'quarantined');self.assertIs(slot['old_handle'],f.handle)
        self.assertIsNotNone(slot['new_handle']);self.assertFalse(m._continuation_credits)
        self.assertNotIn('credit',slot);self.assertTrue(f.enabled.owner.retained(f.handle))
        self.assertFalse(f.app.host.drain(0).all_settled)
