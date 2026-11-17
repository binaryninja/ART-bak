"""Inert coordinator association checks, not proof of live app issuance.

All resources use their normal constructors. The opaque app value is never
served, and no builder, inventory load, admission, pump operation or task runs.
"""
import ast
from contextlib import contextmanager
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from artex.agent.admission import AdmissionController
from artex.agent.bootstrap_setup import BootstrapApplication, build_bootstrap_application
from artex.agent.engine_runner import EngineRunner
from artex.agent.host import AgentHost
from artex.agent.pump import EnginePump
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.manager import TaskManager, RuntimeUnavailable
from artex.server.hosted_setup import HostedApplication, build_hosted_application
from artex.server.task_host import TaskHTTPManagerFacade
from artex.server.task_runtime_setup import TaskRuntimeApplication, build_task_runtime_application
from test_operator_runtime import fixture, no_external_effects


@contextmanager
def inert_only():
    """Observe and deny operations without replacing guards or constructors."""
    forbidden = {function.__code__ for function in (
        build_bootstrap_application, build_hosted_application, build_task_runtime_application,
        TaskManager.load_existing, TaskManager.start, TaskManager.create_task,
        AgentHost.start, AgentHost.create_task, AgentHost.advance_owned, AgentHost.poll,
        EngineRunner.start, EngineRunner.advance, EngineRunner.settle_completed,
        EnginePump.start, EnginePump.stop, EnginePump.tick,
        TaskRuntimeApplication.tick, TaskRuntimeApplication.start_pump,
        TaskRuntimeApplication.stop_pump, AdmissionController.admission_guard,
    )}
    previous = sys.getprofile()
    observed = []
    def profile(frame, event, argument):
        if event == 'call' and frame.f_code in forbidden:
            observed.append(frame.f_code.co_qualname)
            raise AssertionError('Forbidden operation during structural binding check')
        if previous is not None:
            previous(frame, event, argument)
    with no_external_effects() as effects:
        sys.setprofile(profile)
        try:
            yield observed, effects
        finally:
            sys.setprofile(previous)


def make_parts():
    # Normal resolver constructor salt is prepared outside effect observation.
    inputs, calls, deny = fixture()
    def runtime():
        return TaskRuntimeAssembly(inputs.executor, providers=inputs.providers,
            transcript=inputs.transcript, prompts=inputs.prompts,
            graph_catalog=inputs.graph_catalog, limits=inputs.limits,
            policy_revision=inputs.policy_revision, current=inputs.current,
            lifecycle_guard=inputs.lifecycle_guard, authorize=inputs.authorize,
            asset_authorize=inputs.asset_authorize, validate_input=inputs.validate_input,
            activity_authorize=None, cleanup_authorize=None)
    with inert_only() as observation:
        first, second = runtime(), runtime()
        runner = EngineRunner(readiness=deny('readiness'), factory=deny('handle_factory'))
        manager = TaskManager(inputs.providers.tasks, runner=runner)
        host = AgentHost(manager, runner)
        admission = AdmissionController(host, limit=1, single_process=True)
        facade = TaskHTTPManagerFacade(host, admission_guard=admission.admission_guard)
        hosted = HostedApplication(object(), manager, host, runner, facade)
        pump = EnginePump(host, clock=deny('pump_clock'))
    assert observation == ([], []) and calls == []
    return SimpleNamespace(runtime=first, alternate=second, hosted=hosted,
        admission=admission, pump=pump, calls=calls, inputs=inputs)


def application(parts):
    return TaskRuntimeApplication(parts.runtime,
        BootstrapApplication(parts.hosted, parts.admission, coordinator=parts.runtime.coordinator),
        parts.pump)


class OperatorHostBindingTests(unittest.TestCase):
    def check_inert(self, app, calls, *, rejected=False):
        with inert_only() as observation:
            if rejected:
                with self.assertRaises(RuntimeUnavailable):
                    app._check()
            else:
                self.assertIsNone(app._check())
        self.assertEqual(observation, ([], []))
        self.assertEqual(calls, [])

    def test_original_coordinator_pair_passes_structurally_without_operations(self):
        parts = make_parts()
        app = application(parts)
        self.assertIs(app.bootstrap.coordinator, parts.runtime.coordinator)
        self.check_inert(app, parts.calls)
        self.assertIs(app.host, parts.hosted.host)
        self.assertIs(app.manager, parts.hosted.manager)
        self.assertIs(app.runner, parts.hosted.runner)
        self.assertIs(app.facade, parts.hosted.facade)

    def test_valid_other_runtime_on_same_executor_is_rejected(self):
        parts = make_parts()
        app = application(parts)
        with inert_only():
            self.assertIs(parts.alternate.validate_bindings(), True)
        self.assertIs(parts.alternate.executor, parts.runtime.executor)
        self.assertIsNot(parts.alternate.coordinator, parts.runtime.coordinator)
        self.check_inert(replace(app, runtime=parts.alternate), parts.calls, rejected=True)
        self.check_inert(app, parts.calls)

    def test_foreign_retained_coordinator_is_rejected(self):
        parts = make_parts()
        app = application(parts)
        foreign = replace(app.bootstrap, coordinator=parts.alternate.coordinator)
        self.check_inert(replace(app, bootstrap=foreign), parts.calls, rejected=True)
        self.check_inert(app, parts.calls)

    def test_coordinator_is_required_no_fallback_and_record_is_frozen(self):
        parts = make_parts()
        field = next(field for field in fields(BootstrapApplication) if field.name == 'coordinator')
        self.assertIs(field.default, MISSING)
        self.assertIs(field.default_factory, MISSING)
        with self.assertRaises(TypeError):
            BootstrapApplication(parts.hosted, parts.admission)
        app = application(parts)
        with self.assertRaises(FrozenInstanceError):
            app.bootstrap.coordinator = parts.alternate.coordinator
        self.check_inert(replace(app, bootstrap=replace(app.bootstrap, coordinator=None)), parts.calls, rejected=True)

    def test_existing_runtime_internal_binding_check_still_rejects(self):
        parts = make_parts()
        app = application(parts)
        parts.runtime.coordinator = parts.alternate.coordinator
        self.check_inert(app, parts.calls, rejected=True)

    def test_existing_pump_host_association_check_still_rejects(self):
        parts, other = make_parts(), make_parts()
        app = replace(application(parts), pump=other.pump)
        self.check_inert(app, parts.calls, rejected=True)
        self.assertEqual(other.calls, [])

    def test_matching_replacement_pair_is_not_original_live_app_attestation(self):
        parts = make_parts()
        app = application(parts)
        # Explicitly document this limited metadata invariant: no issued-owner
        # receipt or captured callback provenance is claimed by _check().
        renamed_pair = replace(app, runtime=parts.alternate,
            bootstrap=replace(app.bootstrap, coordinator=parts.alternate.coordinator))
        self.check_inert(renamed_pair, parts.calls)

    def test_builder_source_forwards_original_parameter_without_execution(self):
        path = Path(__file__).resolve().parents[1] / 'artex/agent/bootstrap_setup.py'
        module = ast.parse(path.read_text())
        builder = next(node for node in module.body
                       if isinstance(node, ast.FunctionDef) and node.name == 'build_bootstrap_application')
        returned = builder.body[-1]
        self.assertIsInstance(returned, ast.Return)
        call = returned.value
        self.assertIsInstance(call, ast.Call)
        self.assertIsInstance(call.func, ast.Name)
        self.assertEqual(call.func.id, 'BootstrapApplication')
        self.assertEqual([arg.id for arg in call.args], ['hosted', 'controller'])
        self.assertEqual(len(call.keywords), 1)
        self.assertEqual(call.keywords[0].arg, 'coordinator')
        self.assertIsInstance(call.keywords[0].value, ast.Name)
        self.assertEqual(call.keywords[0].value.id, 'coordinator')


if __name__ == '__main__':
    unittest.main()
