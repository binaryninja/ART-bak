import unittest
from datetime import timedelta
from unittest.mock import patch
from contextlib import contextmanager
import test_task_continuation as fixture
from artex.agent.continuation_inspection import PausedHandoffInspector

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.app.host.install_continuation(self.f.owner);self.f.app.host.pause('5',timeout=0)
        self.owner=self.f.owner;self.cap=self.f.app.manager._suspensions['5']['cap']
        self.owner.seal_resume();self.allowed=True;self.hook=lambda:None;self.queries=[];self.db_hook=lambda:None
        self.version=self.f.stamp;self.no_running=True
        original=self.f.db.fetch_all
        def read(q):
            self.queries.append(q)
            if q.sql.startswith('SELECT task.updated_at,journal.run_id'):
                self.db_hook();r=self.f.handle._receipt
                return [(self.version,r.run_id,r.revision,self.no_running)]
            return original(q)
        self.f.db.fetch_all=read
        def allow(task):self.hook();return self.allowed
        self.inspector=PausedHandoffInspector(self.owner,authorize=allow)
    def inspect(self):return self.inspector.inspect(self.cap)
    def test_complete_is_never_release_or_recovery_authority(self):
        before=(self.f.handle,self.f.app.manager._suspensions['5'],list(self.f.f.p.calls))
        result=self.inspect();self.assertEqual(result.state,'observed_prerequisites_complete')
        self.assertFalse(result.recoverable);self.assertFalse(result.release_authorized)
        self.assertEqual(result.task_version,self.version.isoformat())
        self.assertNotIn(self.f.handle._receipt.owner_token,repr(result))
        self.assertEqual(len([q for q in self.queries if q.sql.startswith('SELECT task.updated_at')]),2)
        self.assertTrue(all(q.sql.lstrip().startswith('SELECT') for q in self.queries))
        self.assertIs(self.f.handle,before[0]);self.assertIs(self.f.app.manager._suspensions['5'],before[1])
        self.assertEqual(self.f.f.p.calls,before[2]);self.assertTrue(self.owner.retained(self.f.handle))
        self.assertFalse(self.f.app.host.drain(0).all_settled)
    def test_denied_before_sql(self):
        self.allowed=False
        with self.assertRaises(PermissionError):self.inspect()
        self.assertFalse(self.queries)
    def test_unsealed_and_active_attempts_ineligible(self):
        # Fault fixtures test observations, not a supported unseal operation.
        self.owner._resume_sealed=False
        self.assertEqual(self.inspect().reasons,('resume_not_sealed',));self.assertFalse(self.queries)
        self.owner._resume_sealed=True;self.owner._resume_attempts[object()]=(self.cap,123)
        self.assertEqual(self.inspect().reasons,('resume_attempts_active',));self.assertFalse(self.queries)
    def test_quarantine_and_pending_cleanup(self):
        self.owner.quarantine(self.cap)
        self.assertEqual(self.inspect().reasons,('suspension_not_clean_paused',));self.assertFalse(self.queries)
    def test_database_version_or_running_claim_mismatch(self):
        self.version+=timedelta(seconds=1)
        self.assertEqual(self.inspect().state,'ineligible')
        self.version=self.f.stamp;self.no_running=False
        self.assertEqual(self.inspect().reasons,('durable_state_mismatch',))
    def test_database_exception_sanitized(self):
        def fail():raise RuntimeError('SECRET CONNECTION')
        self.db_hook=fail
        result=self.inspect();self.assertEqual(result.state,'unavailable');self.assertNotIn('SECRET',repr(result))
    def test_final_policy_quarantine_prevents_positive(self):
        calls=[0]
        def revoke():
            calls[0]+=1
            if calls[0]==3:self.owner.quarantine(self.cap)
        self.hook=revoke
        self.assertNotEqual(self.inspect().state,'observed_prerequisites_complete')
    def test_final_policy_version_change_reobserved(self):
        calls=[0]
        def change():
            calls[0]+=1
            if calls[0]==3:self.version+=timedelta(seconds=1)
        self.hook=change
        self.assertEqual(self.inspect().reasons,('durable_state_mismatch',))
    def test_foreign_owner_and_method_refused(self):
        from artex.agent.task_continuation import TaskContinuationOwner
        foreign=TaskContinuationOwner(self.f.app,authorize=lambda p:True)
        with self.assertRaises(RuntimeError):PausedHandoffInspector(foreign,authorize=lambda *a:True)
        with patch.object(self.owner,'_record',foreign._record):
            with self.assertRaises(RuntimeError):self.inspect()
    def test_host_busy_not_eligible(self):
        self.f.app.host._working=True
        try:self.assertEqual(self.inspect().reasons,('host_lifecycle_busy',))
        finally:self.f.app.host._working=False
        self.assertFalse(self.queries)
    def test_cleanup_pending_not_eligible(self):
        cleanup=self.f.handle._child.cleanup;cleanup._records[object()]={}
        self.assertEqual(self.inspect().reasons,('cleanup_pending',));self.assertFalse(self.queries)
    def test_owner_denied_negative_diagnostic_not_disclosed(self):
        self.f.allowed=False;self.owner._resume_sealed=False
        with self.assertRaises(PermissionError):self.inspect()
        self.assertFalse(self.queries)
    def test_final_inspection_grant_revokes_owner(self):
        calls=[0]
        def revoke():
            calls[0]+=1
            if calls[0]==4:self.f.allowed=False
        self.hook=revoke
        with self.assertRaises(PermissionError):self.inspect()
    def test_final_sql_revokes_inspection_grant(self):
        calls=[0]
        def revoke():
            calls[0]+=1
            if calls[0]==2:self.allowed=False
        self.db_hook=revoke
        with self.assertRaises(PermissionError):self.inspect()
    def test_handle_keyed_final_retirement_blocks_observation(self):
        self.f.app.runner._final_retirement[self.f.handle]=object()
        self.assertEqual(self.inspect().reasons,('runner_ownership_changed',))
        self.assertFalse(self.queries)
    def test_process_ownership_not_polled(self):
        marker=object();self.f.handle._child.process=marker;self.f.handle._child._process=marker
        self.assertEqual(self.inspect().reasons,('process_or_engine_unsupported',));self.assertFalse(self.queries)

if __name__=='__main__':unittest.main()
