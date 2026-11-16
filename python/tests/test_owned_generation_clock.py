import unittest
from artex.agent.deadline import OwnedGenerationClock,PreparedDispatchClock,DispatchClockUnavailable,DispatchClockUncertain
from artex.agent.runtime import RuntimeContext,Grant
import test_dispatch_deadline as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.DispatchDeadlineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.shared=OwnedGenerationClock(self.f.f.store,10,monotonic_clock=self.f.helper.monotonic_clock)
    def helper(self,role):
        return PreparedDispatchClock(self.f.f.store,model=self.f.helper.model,role=role,timeout_seconds=10,
            authorize=lambda *a:Grant(True,True,True),binding_current=lambda m:True,
            wall_clock=lambda:self.f.wall,monotonic_clock=self.f.helper.monotonic_clock,generation_clock=self.shared)
    def test_two_roles_one_stamp_and_actual_context_clamp(self):
        planner=self.helper('planner');worker=self.helper('worker')
        clock=self.f.helper.monotonic_clock
        token=type('Token',(),{'cancelled':False})()
        context=RuntimeContext(dict(self.f.ctx),token,160.,clock)
        budget=planner.before_dispatch(context,{'messages':[]},own_budget_seconds=context.remaining_seconds())
        context.clamp_deadline(budget.monotonic_deadline,clock=clock)
        self.assertEqual(context.remaining_seconds(),10.)
        self.f.mono+=3
        context['role']='worker'
        second=worker.before_dispatch(context,{'messages':[]},own_budget_seconds=context.remaining_seconds())
        context.clamp_deadline(second.monotonic_deadline,clock=clock)
        self.assertEqual(context.remaining_seconds(),7.)
        self.assertEqual(sum(q.sql.startswith('UPDATE tasks SET first_run_at') for q in self.f.f.sql),1)
    def test_existing_lease_not_adopted(self):
        self.shared.read_or_stamp()
        with self.assertRaises(ValueError):OwnedGenerationClock(self.f.f.store,10)
    def test_uncertainty_latches_shared_across_roles(self):
        calls=[]
        def stamp(n):calls.append(n);raise DispatchClockUncertain('inert')
        self.f.f.store.stamp_owned=stamp
        with self.assertRaises(DispatchClockUncertain):self.shared.read_or_stamp()
        with self.assertRaises(DispatchClockUncertain):self.shared.read_or_stamp()
        self.assertEqual(calls,[10]);self.assertTrue(self.shared.unresolved)
    def test_busy_does_not_create_second_reservation(self):
        self.shared._lock.acquire()
        try:
            with self.assertRaises(DispatchClockUnavailable):self.shared.read_or_stamp()
        finally:self.shared._lock.release()
        self.assertFalse(self.shared.unresolved);self.assertEqual(self.f.f.sql,[])
    def test_zero_clock_is_inert(self):
        store=self.f.f.store;store.handle._configuration=(*store.handle._configuration[:-1],0);store.handle._task['timeout_seconds']=0
        shared=OwnedGenerationClock(store,0)
        self.assertIsNone(shared.read_or_stamp());self.assertEqual(self.f.f.sql,[])

    def test_new_worker_context_cannot_extend_clock_after_wall_reversal(self):
        from datetime import timedelta
        clock=self.f.helper.monotonic_clock;token=type('Token',(),{'cancelled':False})()
        planner=self.helper('planner');worker=self.helper('worker')
        ctx=RuntimeContext(dict(self.f.ctx),token,160.,clock)
        first=planner.before_dispatch(ctx,{'messages':[]},own_budget_seconds=60.)
        self.f.wall-=timedelta(seconds=50);self.f.mono+=3
        values=dict(self.f.ctx);values['role']='worker'
        fresh=RuntimeContext(values,token,163.,clock)
        second=worker.before_dispatch(fresh,{'messages':[]},own_budget_seconds=60.)
        self.assertEqual(first.monotonic_deadline,110.)
        self.assertEqual(second.monotonic_deadline,110.)
        self.assertEqual(second.remaining_seconds,7.)
