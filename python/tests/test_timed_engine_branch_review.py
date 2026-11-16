"""Independent actual owned timed branch; synthetic positive pins, inert HTTP."""
import unittest
import test_timed_engine_branch as fixture
from artex.agent.deadline import TaskBudgetExpired,DispatchClockUnavailable

class TimedEngineReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def prepare_clock(self,after_snapshot=None):
        f=self.f;f.stamp_original_clock();f.actions.timing.kernel.narrow_deadline(110.,100.)
        db=f.f.f.f.db;original=db.fetch_all;clock=f.actions.timing.store.lease.clock
        def query(q):
            if q.sql.startswith('SELECT t.id,t.first_run_at,t.deadline_at'):
                if after_snapshot:after_snapshot()
                return [(5,clock.first_run_at,clock.deadline_at)]
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=f.actions.receipt
                return [(5,)] if q.params==(r.task_id,r.exploration_id,r.run_id,r.request_id,r.config_digest,r.owner_token,r.revision) else []
            return original(q)
        db.fetch_all=query
    def advance(self):
        f=self.f
        return f.bundle.host.advance_owned(lambda:f.bundle.runner.advance('5',f.handle.token,1.)).value
    def test_expiry_during_snapshot_preserves_events_and_no_claim(self):
        f=self.f
        self.prepare_clock(lambda:f.actions.timing.kernel.narrow_deadline(99.,100.))
        f.engine.notify('review-hint','hint')
        before=list(f.f.f.p.calls)
        result=self.advance()
        self.assertEqual(result.action,'expiry_wait')
        self.assertEqual(f.f.f.p.calls,before)
        self.assertEqual(len(f.engine._events),1)
        self.assertEqual(f.engine.cleanup._records,{})
        self.assertFalse(f.engine.unresolved)
    def test_generic_transport_clock_failure_not_task_expiry(self):
        f=self.f;self.prepare_clock();calls=[]
        def factory(config):
            def invoke(*a):calls.append(1);raise DispatchClockUnavailable('inert unavailable')
            return invoke
        f.f.f.p.factory._transport_factory=factory;f.f.f.p.factory.invalidate()
        result=self.advance()
        self.assertNotEqual(result.action,'expiry_wait');self.assertEqual(calls,[1])
    def test_canonical_commit_failure_retains_expiry_cleanup(self):
        f=self.f;self.prepare_clock();calls=[]
        def factory(config):
            def invoke(*a):calls.append(1);raise TaskBudgetExpired()
            return invoke
        f.f.f.p.factory._transport_factory=factory;f.f.f.p.factory.invalidate()
        def fail(*a):raise RuntimeError('inert persistence failure')
        f.engine.planner.transcript.commit=fail
        with self.assertRaises(Exception):self.advance()
        self.assertTrue(f.engine.unresolved)
        self.assertTrue(f.engine.cleanup._records)
        try:drained=f.bundle.host.drain(0)
        except Exception:pass
        else:self.assertFalse(drained.all_settled)
        with self.assertRaises(Exception):self.advance()
        self.assertEqual(calls,[1])
    def test_removed_exact_manual_registration_refuses_before_work(self):
        f=self.f
        with f.bundle.manual_actions._condition:
            del f.bundle.manual_actions._components[f.actions.observer]
        calls=list(f.f.f.p.calls)
        with self.assertRaises(Exception):self.advance()
        self.assertEqual(f.f.f.p.calls,calls)
        self.assertIsNone(f.actions.timing.store.lease)
