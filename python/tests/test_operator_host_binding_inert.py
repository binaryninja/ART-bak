"""Normal-object structural association tests; no authentic app provenance claim.

The HTTP application slot is an unused opaque sentinel. All lifecycle resources
and wrapper dataclasses are actual normal constructor instances. No builder,
inventory, admission, task execution, pump, lease or cleanup operation runs.
Private directory setup and real resolver entropy precede the observed phase.
"""
import ast
from dataclasses import MISSING, FrozenInstanceError, fields, replace
import inspect
from pathlib import Path
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest

from artex.agent.admission import AdmissionController
from artex.agent.bootstrap import BootstrapCoordinator
from artex.agent.bootstrap_setup import BootstrapApplication, build_bootstrap_application
from artex.agent.engine_runner import EngineRunner
from artex.agent.host import AgentHost
from artex.agent.operator_runtime import compose_operator_runtime
from artex.agent.pump import EnginePump
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.db.tasks import TaskStore
from artex.manager import TaskManager, RuntimeUnavailable
from artex.server.application import build_application
from artex.server.hosted_setup import HostedApplication, build_hosted_application
from artex.server.task_host import TaskHTTPManagerFacade
from artex.server.task_runtime_setup import TaskRuntimeApplication, build_task_runtime_application
from test_operator_runtime_inert_acceptance import (
    FailOnUse, UnexpectedEffect, make_fixture, observe_construction,
)


FORBIDDEN_BINDING_CODES = {function.__code__ for function in (
    build_bootstrap_application, build_hosted_application, build_task_runtime_application,
    build_application, TaskManager.load_existing, TaskStore.list,
    TaskManager.start, TaskManager.create_task, TaskManager.pause, TaskManager.control_task,
    EngineRunner.ready, EngineRunner.start, EngineRunner._prepare, EngineRunner.advance,
    EngineRunner.handles, EngineRunner.settle_completed, AgentHost.start,
    AgentHost.create_task, AgentHost.pause, AgentHost.control_task, AgentHost.poll,
    AgentHost.drain, AgentHost.advance_owned, EnginePump.tick, EnginePump.start,
    EnginePump.stop, TaskHTTPManagerFacade.start, TaskHTTPManagerFacade.create_task,
    TaskHTTPManagerFacade.control_task, Thread.start,
    AdmissionController.admission_guard.__wrapped__,
)}


def observe_binding(*, forbidden_codes=()):
    """Observe existing code without substituting guards or lifecycle methods."""
    return observe_construction(forbidden_codes=FORBIDDEN_BINDING_CODES | set(forbidden_codes))


def make_host_binding_fixture(root):
    """Prepare real resources, then compose only ordinary inert constructors.

    ``root`` is an already-existing private directory. Every readiness, factory,
    owner, DB and transport callback is a raising tripwire. The normal capacity
    and pump constructors register only their new host's process-local identity;
    no registry is cleared and no private active slot is synthesized.
    """
    resources = make_fixture(root)
    callbacks = {name: FailOnUse(name, resources.calls) for name in
                 ('readiness', 'factory', 'dto', 'profile_available', 'pump.clock')}
    with observe_binding() as observation:
        runtime = compose_operator_runtime(resources.inputs)
        runner = EngineRunner(readiness=callbacks['readiness'], factory=callbacks['factory'], max_pending=2)
        manager = TaskManager(resources.tasks, runner=runner, dto_enricher=callbacks['dto'],
                              profile_available=callbacks['profile_available'])
        host = AgentHost(manager, runner)
        controller = AdmissionController(host, limit=1, single_process=True)
        admission_guard = controller.admission_guard
        facade = TaskHTTPManagerFacade(host, admission_guard=admission_guard)
        http_placeholder = object()
        hosted = HostedApplication(http_placeholder, manager, host, runner, facade)
        bootstrap = BootstrapApplication(hosted, controller, coordinator=runtime.coordinator)
        pump = EnginePump(host, interval=1.0, max_per_tick=1, clock=callbacks['pump.clock'])
        application = TaskRuntimeApplication(runtime, bootstrap, pump)
        application._check()
    return SimpleNamespace(resources=resources, runtime=runtime, runner=runner,
        manager=manager, host=host, controller=controller, facade=facade, hosted=hosted,
        bootstrap=bootstrap, pump=pump, application=application,
        observation=observation, callbacks=callbacks, admission_guard=admission_guard,
        http_placeholder=http_placeholder)


class OperatorHostBindingInertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='artex-host-binding-')
        self.addCleanup(self.temp.cleanup)
        self.f = make_host_binding_fixture(Path(self.temp.name))
        self.assert_observation(self.f.observation, 1)

    def assert_idle(self, f=None):
        f = self.f if f is None else f
        self.assertEqual(f.resources.calls, [])
        self.assertEqual(f.manager._tasks, {})
        self.assertEqual(f.manager._runs, {})
        self.assertEqual(f.runner._handles, {})
        self.assertEqual(f.runtime._configurations, {})
        self.assertIsNone(f.pump._thread)
        self.assertEqual(f.pump._active, 0)
        self.assertFalse(f.host._working)
        self.assertFalse(f.host._settling)
        self.assertEqual(f.host._mutations, 0)
        self.assertEqual(f.host._admissions, 0)
        self.assertFalse(f.controller._busy)
        self.assertIsNone(f.controller._active_claim)
        self.assertFalse(f.resources.store._runtime_started)
        self.assertIsNone(f.resources.store._runtime_lease)
        self.assertEqual(f.resources.factory._cache, {})

    def assert_observation(self, state, constructors=0):
        self.assertEqual(state, {'assembly_constructions': constructors,
                                 'effects': [], 'forbidden_calls': []})
        self.assert_idle()
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def second_runtime(self):
        with observe_binding() as observation:
            second = compose_operator_runtime(self.f.resources.inputs)
            self.assertTrue(second.validate_bindings())
        self.assert_observation(observation, 1)
        self.assertIsNot(second, self.f.runtime)
        self.assertIs(second.executor, self.f.runtime.executor)
        self.assertIsNot(second.coordinator, self.f.runtime.coordinator)
        return second

    def test_all_resource_and_wrapper_types_are_actual_normal_objects(self):
        f = self.f
        for value, expected in [(f.runtime, TaskRuntimeAssembly), (f.runner, EngineRunner),
            (f.manager, TaskManager), (f.host, AgentHost), (f.controller, AdmissionController),
            (f.facade, TaskHTTPManagerFacade), (f.hosted, HostedApplication),
            (f.bootstrap, BootstrapApplication), (f.pump, EnginePump),
            (f.application, TaskRuntimeApplication), (f.runtime.coordinator, BootstrapCoordinator)]:
            self.assertIs(type(value), expected)
        self.assertIs(f.hosted.app, f.http_placeholder)
        self.assertIs(f.bootstrap.coordinator, f.runtime.coordinator)
        self.assertIs(f.runner._host, f.host)
        self.assertIs(f.runner._host_manager, f.manager)
        self.assertIs(f.runner._host_executor, f.runtime.executor)
        self.assertIs(f.manager.repository, f.resources.tasks)
        self.assertIs(f.manager.repository.executor, f.runtime.executor)
        self.assertIs(f.facade._admission, f.admission_guard)
        self.assertIs(f.admission_guard.__self__, f.controller)
        self.assertIs(f.runner._host_work_check.__self__, f.host)
        self.assertIs(f.runner._host_work_check.__func__, AgentHost.owns_work)
        self.assertIs(f.runner._host_settlement_check.__self__, f.host)
        self.assertIs(f.runner._host_settlement_check.__func__, AgentHost.owns_settlement)

    def test_repeated_original_check_preserves_claim_and_has_no_work_slot(self):
        f = self.f
        captured = (f.runner._host_work_check, f.runner._host_settlement_check)
        with observe_binding() as observation:
            for _ in range(3):
                self.assertIsNone(f.application._check())
                self.assertIsNone(f.runner.claim_host(f.host))
                self.assertIs(f.host.owns_work(f.runner), False)
                self.assertIs(f.host.owns_settlement(f.runner), False)
                self.assertTrue(f.runtime.validate_bindings())
        self.assertIs(f.runner._host_work_check, captured[0])
        self.assertIs(f.runner._host_settlement_check, captured[1])
        self.assert_observation(observation)

    def test_coordinator_metadata_is_required_and_immutable(self):
        f = self.f
        field = next(field for field in fields(BootstrapApplication) if field.name == 'coordinator')
        self.assertIs(field.default, MISSING)
        self.assertIs(field.default_factory, MISSING)
        with observe_binding() as observation:
            with self.assertRaises(TypeError):
                BootstrapApplication(f.hosted, f.controller)
            with self.assertRaises(FrozenInstanceError):
                f.bootstrap.coordinator = f.runtime.coordinator
        self.assert_observation(observation)

    def test_other_valid_runtime_same_executor_is_rejected(self):
        f, other = self.f, self.second_runtime()
        with observe_binding() as observation:
            switched = replace(f.application, runtime=other)
            self.assertIs(switched.bootstrap, f.bootstrap)
            with self.assertRaises(RuntimeUnavailable):
                switched._check()
            f.application._check()
        self.assert_observation(observation)

    def test_foreign_coordinator_is_rejected_even_with_fresh_wrapper(self):
        f, other = self.f, self.second_runtime()
        with observe_binding() as observation:
            bad_bootstrap = replace(f.bootstrap, coordinator=other.coordinator)
            with self.assertRaises(RuntimeUnavailable):
                replace(f.application, bootstrap=bad_bootstrap)._check()
            f.application._check()
        self.assert_observation(observation)

    def test_same_coordinator_replacement_wrappers_are_structurally_valid_only(self):
        f = self.f
        with observe_binding() as observation:
            copied_hosted = replace(f.hosted)
            copied_bootstrap = replace(f.bootstrap, hosted=copied_hosted)
            copied_application = replace(f.application, bootstrap=copied_bootstrap)
            copied_application._check()
            self.assertIs(copied_application.bootstrap.coordinator, f.runtime.coordinator)
        # This positive demonstrates identity agreement, not trusted builder origin.
        self.assertIsNot(copied_application, f.application)
        self.assert_observation(observation)

    def test_matching_outer_metadata_cannot_override_runtime_internal_coordinator_pin(self):
        f, other = self.f, self.second_runtime()
        original = f.runtime.coordinator
        with observe_binding() as observation:
            f.runtime.coordinator = other.coordinator
            try:
                matching_bootstrap = replace(f.bootstrap, coordinator=other.coordinator)
                with self.assertRaises(RuntimeUnavailable):
                    replace(f.application, bootstrap=matching_bootstrap)._check()
            finally:
                f.runtime.coordinator = original
            f.application._check()
        self.assert_observation(observation)

    def test_existing_foreign_pump_and_admission_host_checks_remain(self):
        f = self.f
        other = make_host_binding_fixture(Path(self.temp.name))
        self.assertEqual(other.observation, {'assembly_constructions': 1, 'effects': [], 'forbidden_calls': []})
        with observe_binding() as observation:
            with self.assertRaises(RuntimeUnavailable):
                replace(f.application, pump=other.pump)._check()
            with self.assertRaises(RuntimeUnavailable):
                replace(f.application, bootstrap=replace(f.bootstrap, admission=other.controller))._check()
            f.application._check()
            other.application._check()
        self.assert_idle(other)
        self.assert_observation(observation)

    def test_matching_coordinator_does_not_remove_existing_executor_check(self):
        f = self.f
        other = make_host_binding_fixture(Path(self.temp.name))
        self.assertIsNot(other.runtime.executor, f.runtime.executor)
        with observe_binding() as observation:
            matching_bootstrap = replace(f.bootstrap, coordinator=other.runtime.coordinator)
            switched = replace(f.application, runtime=other.runtime, bootstrap=matching_bootstrap)
            with self.assertRaises(RuntimeUnavailable):
                switched._check()
            f.application._check()
            other.application._check()
        self.assert_idle(other)
        self.assert_observation(observation)

    def test_second_normal_host_cannot_steal_original_runner_claim(self):
        f = self.f
        captured = (f.runner._host, f.runner._host_manager, f.runner._host_executor)
        with observe_binding() as observation:
            other_manager = TaskManager(f.resources.tasks, runner=f.runner)
            with self.assertRaises(RuntimeUnavailable):
                AgentHost(other_manager, f.runner)
            f.runner.claim_host(f.host)
            f.application._check()
        for actual, original in zip((f.runner._host, f.runner._host_manager, f.runner._host_executor), captured):
            self.assertIs(actual, original)
        self.assertEqual(other_manager._tasks, {})
        self.assertEqual(other_manager._runs, {})
        self.assert_observation(observation)

    def test_original_builder_return_forwards_parameter_without_executing_builder(self):
        tree = ast.parse(inspect.getsource(build_bootstrap_application))
        returns = [node.value for node in ast.walk(tree) if isinstance(node, ast.Return)
                   and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == 'BootstrapApplication']
        self.assertEqual(len(returns), 1)
        keywords = [kw for kw in returns[0].keywords if kw.arg == 'coordinator']
        self.assertEqual(len(keywords), 1)
        self.assertIsInstance(keywords[0].value, ast.Name)
        self.assertEqual(keywords[0].value.id, 'coordinator')
        self.assert_idle()

    def test_instrumentation_detects_inert_controls_without_guard_substitution(self):
        before = sys.getprofile()
        with self.assertRaises(UnexpectedEffect):
            with observe_binding() as audit:
                sys.audit('open', str(Path(self.temp.name, 'never-opened')), 'r', 0)
        self.assertEqual(audit['effects'], ['open'])
        self.assertIs(sys.getprofile(), before)

        def harmless_probe():
            self.fail('Forbidden function body ran')

        with self.assertRaises(UnexpectedEffect):
            with observe_binding(forbidden_codes={harmless_probe.__code__}) as calls:
                harmless_probe()
        self.assertEqual(len(calls['forbidden_calls']), 1)
        self.assertIs(sys.getprofile(), before)
        self.assert_idle()
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
