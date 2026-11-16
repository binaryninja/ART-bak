"""Opt-in timing composition only; positive admission remains unsupported."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime,timezone
import unittest
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.agent.task_timing import TaskTimingPolicy,PreparedTaskTiming,TimingUnavailable
from artex.agent.engine import EngineScope
from artex.agent.runtime import Grant
from artex.agent.goals import GoalScope
from artex.manager import CancellationToken,RuntimeUnavailable
import test_task_runtime as fixture


class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.calls=[]
        self.policy=TaskTimingPolicy('timing-v1',lambda:100.,lambda:datetime(2026,1,1,tzinfo=timezone.utc),
            lambda *a:self.calls.append('stamp') or True,lambda *a:self.calls.append('dispatch') or Grant(True,True,True))
    def make(self,policy=None,cleanup=True):
        a=self.f.assembly
        return TaskRuntimeAssembly(a.executor,providers=a.providers,transcript=a.transcript,prompts=a._prompts,
            policy_revision=a._revision,current=a._current,lifecycle_guard=a._guard,authorize=a._authorize,
            asset_authorize=a._assets,validate_input=a._input,graph_catalog=a._catalog,limits=a._limits,
            cleanup_authorize=(lambda *a:True) if cleanup else None,timing_policy=policy)
    def engine(self,a):
        conf=a.bootstrap_configuration(self.f.p.snapshot)
        return a.engine_factory(self.f.p.snapshot,CancellationToken(),EngineScope(5,9,71,conf.policy_revision),lambda *a:True,lambda *a:nullcontext())
    def test_policy_requires_exact_type_and_cleanup(self):
        with self.assertRaises(ValueError):self.make(object())
        with self.assertRaises(ValueError):self.make(self.policy,False)
        self.assertEqual(self.calls,[]);self.assertEqual(self.f.p.calls,[])
    def test_optin_changes_digest_and_revision_is_durable(self):
        plain=self.make();timed=self.make(self.policy)
        a=plain.bootstrap_configuration(self.f.p.snapshot)
        b=timed.bootstrap_configuration(self.f.p.snapshot)
        c=self.make(replace(self.policy,revision='timing-v2')).bootstrap_configuration(self.f.p.snapshot)
        self.assertNotEqual(a.policy_revision,b.policy_revision)
        self.assertNotEqual(b.policy_revision,c.policy_revision)
        self.assertNotIn('lambda',repr(b));self.assertEqual(self.calls,[])
    def test_explicit_none_preserves_default_configuration(self):
        self.assertEqual(self.f.assembly.bootstrap_configuration(self.f.p.snapshot),
                         self.make(None,False).bootstrap_configuration(self.f.p.snapshot))
    def test_inert_engine_has_one_timing_object_and_identical_role_clocks(self):
        a=self.make(self.policy);engine=self.engine(a)
        self.assertIs(type(engine.timing),PreparedTaskTiming)
        self.assertIs(engine.timing.policy,self.policy)
        self.assertIs(engine.planner.clock,self.policy.clock);self.assertIs(engine.worker.clock,self.policy.clock)
        self.assertIsNone(engine.timing.store);self.assertIsNone(engine.timing.kernel)
        self.assertEqual(self.calls,[]);self.assertEqual(self.f.p.construct,[])
    def test_goals_binding_is_unwrapped(self):
        a=self.make(self.policy);conf=a.bootstrap_configuration(self.f.p.snapshot)
        scope=GoalScope(5,9,71,conf.policy_revision,conf.model,conf.system)
        goals=a._goals(scope,lambda s:True,lambda s:nullcontext())
        from artex.agent.task_deadline_provider import DeadlineProvider
        binding=goals.resolver(scope,conf.model)
        self.assertNotIsInstance(binding.provider,DeadlineProvider)
        self.assertEqual(self.calls,[]);self.assertEqual(self.f.p.construct,[])
    def test_unbound_execution_provider_refuses_before_transport(self):
        engine=self.engine(self.make(self.policy))
        with self.assertRaises(TimingUnavailable):engine.planner.provider(engine.planner_scope,engine.planner_scope.model)
        self.assertEqual(self.f.p.construct,[]);self.assertEqual(self.f.p.calls,[])
    def test_policy_replacement_or_callback_mutation_fails_closed(self):
        a=self.make(self.policy);a._timing_policy=replace(self.policy,revision='new')
        with self.assertRaises(RuntimeUnavailable):a.validate_bindings()
        a=self.make(self.policy)
        object.__setattr__(self.policy,'stamp_authorize',lambda *a:True)
        with self.assertRaises(RuntimeUnavailable):a.validate_bindings()


class ActualApplicationTests(unittest.TestCase):
    def test_zero_timeout_authenticated_goals_and_owned_timing_bind_are_inert(self):
        import test_task_runtime_application as fixture
        f=fixture.Tests();f.setUp();self.addCleanup(f.doCleanups)
        old=f.runtime;calls=[]
        policy=TaskTimingPolicy('explicit-zero-v1',lambda:100.,lambda:datetime(2026,1,1,tzinfo=timezone.utc),
            lambda *a:calls.append('stamp') or True,lambda *a:calls.append('dispatch') or Grant(True,True,True))
        f.runtime=TaskRuntimeAssembly(old.executor,providers=old.providers,transcript=old.transcript,
            prompts=old._prompts,policy_revision=old._revision,current=old._current,lifecycle_guard=old._guard,
            authorize=old._authorize,asset_authorize=old._assets,validate_input=old._input,
            graph_catalog=old._catalog,cleanup_authorize=lambda *a:True,timing_policy=policy)
        f.test_jwt_create_then_explicit_tick_uses_actual_goals_transport()
        handle=f.bundle.runner.handles()[0];engine=handle._child
        self.assertIs(engine.timing.policy,policy)
        self.assertIsNotNone(engine.timing._bound)
        self.assertIsNone(engine.timing.store);self.assertIsNone(engine.timing.kernel)
        self.assertIs(engine.planner.clock,policy.clock);self.assertIs(engine.worker.clock,policy.clock)
        self.assertEqual(calls,[])
