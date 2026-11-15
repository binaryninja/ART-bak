"""Clock helper only; positive timeout remains unsupported by engine setup."""
import unittest
from datetime import datetime,timezone,timedelta
from dataclasses import replace
from artex.agent.deadline import *
from artex.agent.runtime import Grant,RunStopped
from artex.db.task_deadline import DeadlineUncertain
import test_task_deadline as fixture
from test_chat_runtime import MODEL

class Context(dict):
    cancelled=False
    def check(self):
        if self.cancelled:raise RunStopped('cancelled')

class DispatchDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.wall=self.f.clock;self.mono=100.;self.bound=True;self.allowed=True;self.deadline=self.wall+timedelta(seconds=10)
        self.f.timeout=10;original=self.f.db.fetch_all
        def query(q):
            if q.sql.startswith('SELECT timeout_seconds,first_run_at'):
                self.f.sql.append(q);return [(self.f.timeout,self.f.clock,self.deadline)]
            result=original(q)
            if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(self.f.clock,self.deadline)]
            return result
        self.f.db.fetch_all=query
        self.ctx=Context(task_id=5,exploration_id=9,run_id=71,revision='r1',role='planner')
        self.helper=self.make()
    def make(self,timeout=10,**kw):
        args=dict(model=MODEL,role='planner',timeout_seconds=timeout,authorize=lambda *a:Grant(self.allowed,True,True),binding_current=lambda m:self.bound,wall_clock=lambda:self.wall,monotonic_clock=lambda:self.mono)
        args.update(kw);return PreparedDispatchClock(self.f.store,**args)
    def call(self,**kw):return self.helper.before_dispatch(self.ctx,{'messages':[]},**kw)
    def test_constructor_inert_first_stamp_then_readonly_revalidation(self):
        self.assertEqual(self.f.sql,[])
        a=self.call();self.assertEqual(a.remaining_seconds,10);self.assertEqual(a.monotonic_deadline,110);self.assertTrue(a.task_limited)
        self.wall+=timedelta(seconds=3);self.mono+=3
        b=self.call();self.assertEqual(b.remaining_seconds,7)
        self.assertEqual(sum(q.sql.startswith('UPDATE tasks SET first_run_at') for q in self.f.sql),1)
        self.assertTrue(any(q.sql.startswith('SELECT timeout_seconds,first_run_at') for q in self.f.sql))
    def test_zero_timeout_never_stamps(self):
        self.helper=self.make(0);result=self.call()
        self.assertEqual(self.f.sql,[]);self.assertIsNone(result.deadline_at);self.assertIsNone(result.remaining_seconds)
    def test_scope_and_permission_fail_before_stamp(self):
        self.ctx['role']='goals'
        with self.assertRaises(DispatchClockUnavailable):self.call()
        self.ctx['role']='planner';self.allowed=False
        with self.assertRaises(RunStopped):self.call()
        self.assertEqual(self.f.sql,[])
    def test_expired_is_not_zero_unlimited(self):
        self.wall+=timedelta(seconds=11)
        with self.assertRaises(TaskBudgetExpired):self.call()
        self.assertFalse(self.helper.unresolved)
    def test_own_budget_clamps_and_zero_rejected(self):
        result=self.call(own_budget_seconds=3)
        self.assertEqual(result.remaining_seconds,3);self.assertFalse(result.task_limited)
        with self.assertRaises(ValueError):self.call(own_budget_seconds=0)
    def test_backwards_wallclock_cannot_extend_local_budget(self):
        self.call();self.wall-=timedelta(seconds=50);self.mono+=4
        self.assertEqual(self.call().remaining_seconds,6)
    def test_naive_persisted_date_latches_without_retry(self):
        self.deadline=self.deadline.replace(tzinfo=None)
        with self.assertRaises(DispatchClockUncertain):self.call()
        count=len(self.f.sql)
        with self.assertRaises(DispatchClockUncertain):self.call()
        self.assertEqual(len(self.f.sql),count)
    def test_profile_revoked_by_stamp_blocks_return(self):
        original=self.f.store.stamp
        def stamp(*a):result=original(*a);self.bound=False;return result
        self.f.store.stamp=stamp
        with self.assertRaises(DispatchClockUnavailable):self.call()
        self.assertTrue(self.helper.unresolved)
    def test_uncertain_stamp_never_retried(self):
        calls=[]
        def stamp(*a):calls.append(1);raise DeadlineUncertain('inert uncertain write')
        self.f.store.stamp=stamp
        with self.assertRaises(DeadlineUncertain):self.call()
        with self.assertRaises(DispatchClockUncertain):self.call()
        self.assertEqual(calls,[1])
    def test_changed_persisted_clock_after_cache_rejected(self):
        self.call();self.deadline+=timedelta(seconds=20)
        with self.assertRaises(DispatchClockUncertain):self.call()
    def test_no_nested_lifetime_guard(self):
        from contextlib import contextmanager
        from artex.db.task_deadline import TaskDeadlineStore
        held=False
        @contextmanager
        def guard(*a):
            nonlocal held
            if held:raise AssertionError('nested guard')
            held=True
            try:yield
            finally:held=False
        self.f.handle.coordinator.guard=guard
        self.f.store=TaskDeadlineStore(self.f.db,manager=self.f.f.f.manager,handle=self.f.handle,receipt=self.f.receipt,lifecycle_guard=guard,authorize=lambda *a:True)
        self.helper=self.make();self.call();self.call()
