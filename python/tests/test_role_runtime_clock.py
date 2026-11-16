"""Explicit clock propagation through actual roles; inert providers only."""
import unittest
from artex.agent.planner import PlannerCycle
from artex.agent.runtime import Grant
import test_planner_cycle as p
import test_worker_cycle as w

class Tests(unittest.TestCase):
    def planner(self,clock):
        f=p.PlannerTests();f.setUp();self.addCleanup(f.doCleanups)
        old=f.core
        f.core=PlannerCycle(store=old.store,transcript=old.transcript,current=old.current,lifecycle_guard=old.guard,
            provider_resolver=old.provider,authorize=old.authorize,asset_authorize=old.asset_authorize,clock=clock)
        return f
    def worker(self,clock):
        f=w.WorkerCycleTests();f.setUp();self.addCleanup(f.doCleanups);f.worker=f.new_worker(clock=clock);return f
    def test_actual_context_identity_and_common_shortening(self):
        now=[100.];clock=lambda:now[0];seen=[]
        planner=self.planner(clock);worker=self.worker(clock)
        for f in (planner,worker):
            original=f.provider.complete
            def call(context,request,original=original):
                self.assertIs(context.clock,clock)
                context.clamp_deadline(107.,clock=clock)
                seen.append(context.remaining_seconds())
                return original(context,request)
            f.provider.complete=call
        planner.core.run(planner.core.prepare(planner.scope,'clock'))
        now[0]=103.;worker.worker.run(worker.scope,'clock')
        self.assertEqual(seen,[7.,7.,4.])
    def test_planner_resolver_clock_swap_denies_transport(self):
        f=self.planner(lambda:100.);original=f.core.provider
        def resolver(*args):
            f.core.clock=lambda:100.;return original(*args)
        f.core.provider=resolver
        with self.assertRaises(Exception):f.core.run(f.core.prepare(f.scope,'swap'))
        self.assertEqual(f.provider.calls,[])
    def test_worker_resolver_clock_swap_denies_transport(self):
        f=self.worker(lambda:100.);original=f.worker.provider
        def resolver(*args):
            f.worker.clock=lambda:100.;return original(*args)
        f.worker.provider=resolver
        with self.assertRaises(Exception):f.worker.run(f.scope,'swap')
        self.assertEqual(f.provider.calls,[])
    def test_invalid_clock_rejected_without_call(self):
        for value in (False,1,'clock'):
            with self.assertRaises(ValueError):self.planner(value)
            with self.assertRaises(ValueError):self.worker(value)
