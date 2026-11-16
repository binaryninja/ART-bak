import unittest
from threading import Event,Thread
from unittest.mock import patch
from artex.agent.task_continuation import ContinuationUnavailable
from artex.manager import RuntimeUnavailable
import test_task_continuation as continuation
import test_runtime_listener as listener
from artex.agent.task_continuation import TaskContinuationOwner
from artex.server.runtime_listener import RuntimeListener

class Tests(unittest.TestCase):
    def fixture(self):
        f=continuation.Tests();f.setUp();self.addCleanup(f.doCleanups)
        f.app.host.install_continuation(f.owner);f.app.host.pause('5',timeout=0)
        return f
    def test_seal_idempotent_rejects_without_paused_mutation(self):
        f=self.fixture();before=len(f.db.calls);old=f.app.manager._suspensions['5']
        self.assertTrue(f.owner.seal_resume());self.assertTrue(f.owner.seal_resume())
        with self.assertRaises(ContinuationUnavailable):f.app.host.resume_suspended('5')
        self.assertEqual(len(f.db.calls),before);self.assertIs(f.app.manager._suspensions['5'],old)
        self.assertEqual(old['phase'],'paused');self.assertNotIn('5',f.app.manager._runs)
        self.assertTrue(f.owner.retained(f.handle));self.assertTrue(f.owner.wait_resumes(0))
        self.assertFalse(f.app.host.drain(0).all_settled)
    def test_wait_requires_seal(self):
        f=self.fixture()
        with self.assertRaises(ContinuationUnavailable):f.owner.wait_resumes(0)
        for bad in (-1,True,float('nan')):
            with self.assertRaises(ValueError):f.owner.wait_resumes(bad)
    def test_seal_during_resume_callback_retains_uncertain(self):
        f=self.fixture();before=list(f.f.p.calls)
        # The original callback closes over mutable allowed; hook the configured
        # runtime current observer, without replacing owner implementation.
        entered=Event();release=Event();errors=[]
        # Block SQL read after an issued outer resume claim instead of patching
        # owner methods (which are intentionally anchored).
        fetch=f.db.fetch_all
        def blocked(q):
            if q.sql.startswith('SELECT updated_at FROM tasks'):
                entered.set();release.wait(3)
            return fetch(q)
        f.db.fetch_all=blocked
        def resume():
            try:f.app.host.resume_suspended('5')
            except BaseException as e:errors.append(e)
        t=Thread(target=resume);t.start();self.assertTrue(entered.wait(2))
        try:
            self.assertTrue(f.owner.seal_resume());self.assertFalse(f.owner.wait_resumes(.01))
        finally:release.set();t.join(3)
        self.assertFalse(t.is_alive());self.assertTrue(errors);self.assertIsInstance(errors[0],RuntimeUnavailable)
        self.assertTrue(f.owner.wait_resumes(0));self.assertTrue(f.owner.retained(f.handle))
        self.assertEqual(f.app.manager._suspensions['5']['phase'],'quarantined');self.assertEqual(f.f.p.calls,before)
    def test_capacity_credit_after_seal_is_not_issued(self):
        import test_continuation_capacity as capacity
        c=capacity.Tests();f=c.make();self.addCleanup(c.doCleanups)
        before=len(f.f.f.db.calls);f.enabled.owner.seal_resume()
        self.assertNotEqual(c.resume(f)[0],200)
        self.assertFalse(any(q.sql.startswith("UPDATE") for q in f.f.f.db.calls[before:]))
        self.assertFalse(f.app.manager._continuation_credits)
        self.assertEqual(f.app.manager._suspensions['5']['phase'],'paused')
    def test_issued_credit_is_invalidated_without_consuming_pause(self):
        import test_continuation_capacity as capacity
        c=capacity.Tests();f=c.make();self.addCleanup(c.doCleanups);m=f.app.manager
        with f.app.admission.admission_guard('resume','5'):
            credit=m._suspensions['5']['credit'];f.enabled.owner.seal_resume()
            self.assertFalse(m.validate_continuation_admission(credit))
            with self.assertRaises(ContinuationUnavailable):f.app.host.resume_suspended('5')
            self.assertEqual(m._suspensions['5']['phase'],'paused')
        self.assertFalse(m._continuation_credits)
    def test_seal_after_resume_cas_retains_ambiguous_owners(self):
        f=self.fixture();fetch=f.db.fetch_all;writes=[]
        def sealed_after_write(q):
            result=fetch(q)
            if q.sql.startswith('UPDATE tasks SET paused=false'):
                writes.append(1);f.owner.seal_resume()
            return result
        f.db.fetch_all=sealed_after_write
        with self.assertRaises(RuntimeUnavailable):f.app.host.resume_suspended('5')
        self.assertEqual(writes,[1]);self.assertEqual(f.app.manager._suspensions['5']['phase'],'quarantined')
        self.assertTrue(f.owner.retained(f.handle));self.assertTrue(f.owner.wait_resumes(0))
        self.assertFalse(f.app.host.drain(0).all_settled)
    def test_seal_during_activation_sql_refuses_consumption(self):
        f=self.fixture();f.app.host.resume_suspended('5');new=f.app.runner.handles()[0]
        fetch=f.db.fetch_all
        def sealed(q):
            result=fetch(q)
            if q.sql.startswith('SELECT updated_at FROM tasks'):f.owner.seal_resume()
            return result
        f.db.fetch_all=sealed
        from artex.agent.bootstrap import BootstrapUnresolved
        with self.assertRaises(BootstrapUnresolved):
            f.app.host.advance_owned(lambda:f.app.runner.advance('5',new.token,1.))
        self.assertTrue(f.owner._resume_sealed)
        self.assertTrue(f.owner.retained(new))
        self.assertFalse(any(rec['state']=='consumed' for rec in f.owner._records.values()))
    def test_quarantine_at_final_activation_observation_stays_retained(self):
        f=self.fixture();f.app.host.resume_suspended('5');new=f.app.runner.handles()[0]
        cap=next(cap for cap,rec in f.owner._records.items() if rec['new'] is new)
        original=f.app.manager.owns_generation;calls=[0]
        def quarantine(*a):
            result=original(*a);calls[0]+=1
            if calls[0]==2:f.owner.quarantine(cap)
            return result
        with patch.object(f.app.manager,'owns_generation',side_effect=quarantine):
            with self.assertRaises(ContinuationUnavailable):f.owner.activate(cap,new)
        self.assertEqual(f.owner._records[cap]['state'],'quarantined')
        self.assertTrue(f.owner._records[cap]['failed'])
        self.assertTrue(f.owner.retained(f.handle));self.assertTrue(f.owner.retained(new))
    def test_snapshot_copy_failure_does_not_leak_attempt(self):
        f=self.fixture()
        with patch('artex.manager.deepcopy',side_effect=ValueError('copy failed')):
            with self.assertRaises(ValueError):f.app.host.resume_suspended('5')
        self.assertFalse(f.owner._resume_attempts);self.assertEqual(f.app.manager._suspensions['5']['phase'],'paused')
    def test_listener_seals_before_callbacks_no_task_owners(self):
        f=listener.Tests();f.setUp();self.addCleanup(f.doCleanups)
        owner=TaskContinuationOwner(f.bundle,authorize=lambda p:True)
        f.bundle.host.install_continuation(owner)
        l=RuntimeListener(f.bundle,f.owner)
        # Original callback records order; observe sealed state without changing
        # listener-owned callback identity.
        with patch.object(f.bundle.host,'drain',wraps=f.bundle.host.drain) as drain:
            self.assertTrue(l.stop(1).stopped)
            self.assertTrue(owner._resume_sealed);self.assertTrue(owner.wait_resumes(0))
            drain.assert_called_once()
    def test_listener_rejects_owner_installed_after_binding(self):
        f=listener.Tests();f.setUp();self.addCleanup(f.doCleanups)
        l=RuntimeListener(f.bundle,f.owner);owner=TaskContinuationOwner(f.bundle,authorize=lambda p:True)
        f.bundle.host.install_continuation(owner)
        result=l.stop(0);self.assertFalse(result.stopped);self.assertEqual(result.reason,'ownership_changed')
        self.assertFalse(f.calls)

if __name__=='__main__':unittest.main()
