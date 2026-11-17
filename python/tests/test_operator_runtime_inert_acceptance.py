"""Actual-class, construction-only acceptance; no application or bootstrap runs.

Preparation creates a private existing directory and actual inert resources;
TaskProviderResolver keeps its normal random salt. Only the subsequent helper
call is measured for effects. Nothing replaces filesystem or ownership guards.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import MISSING, fields, replace
import inspect
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

from artex.agent.bootstrap import BootstrapCoordinator, BootstrapJournal
from artex.agent.operator_runtime import OperatorRuntimeInputs, compose_operator_runtime
from artex.agent.runtime import Limits
from artex.agent.task_providers import TaskProviderResolver
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.db.agents import AgentStore
from artex.db.profiles import ProfileStore
from artex.db.tasks import TaskStore
from artex.db.tool_catalog import CatalogTool
from artex.providers import Provider
from artex.providers.factory import ProfileFactory
from artex.transcripts import TranscriptStore


class UnexpectedEffect(AssertionError):
    pass


class FailOnUse:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    def __call__(self, *args, **kwargs):
        self.calls.append(self.name)
        raise UnexpectedEffect(self.name)


class DenyExecutor:
    """An explicit injected executor whose methods never grant or return data."""
    def __init__(self, calls):
        self.calls = calls

    def fetch_all(self, *args, **kwargs):
        return FailOnUse('db.fetch_all', self.calls)(*args, **kwargs)

    def fetch_one(self, *args, **kwargs):
        return FailOnUse('db.fetch_one', self.calls)(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return FailOnUse('db.execute', self.calls)(*args, **kwargs)

    def transaction(self, *args, **kwargs):
        return FailOnUse('db.transaction', self.calls)(*args, **kwargs)


def make_fixture(root):
    """Prepare real resources outside observation, for this and external reviews.

    ``root`` must already be an ordinary private absolute directory. No store
    read/write, lease acquisition, profile validation or provider build occurs.
    """
    root = Path(root)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('Prepare an existing private absolute directory first')
    calls = []
    executor = DenyExecutor(calls)
    tasks, agents, profiles = TaskStore(executor), AgentStore(executor), ProfileStore(executor)
    callbacks = {name: FailOnUse(name, calls) for name in (
        'transport_factory', 'clock', 'sleep', 'resolver_current', 'resolver_guard',
        'authorize_task', 'authorize_profile', 'recovery_guard', 'current',
        'lifecycle_guard', 'authorize', 'asset_authorize', 'validate_input',
        'activity_authorize', 'cleanup_authorize')}
    factory = ProfileFactory(callbacks['transport_factory'], clock=callbacks['clock'], sleep=callbacks['sleep'])
    providers = TaskProviderResolver(tasks, agents, profiles, factory,
        current=callbacks['resolver_current'], lifecycle_guard=callbacks['resolver_guard'],
        authorize_task=callbacks['authorize_task'], authorize_profile=callbacks['authorize_profile'],
        allow_global=False)
    store = TranscriptStore(str(root), max_bytes=1024 * 1024)
    transcript = ChatTranscriptAdapter(store, recovery_guard=callbacks['recovery_guard'])
    prompts = {'goals': 'Inert goals fixture.', 'planner': 'Inert planner fixture.', 'worker': 'Inert worker fixture.'}
    catalog = {role: [CatalogTool('FixtureInspect', True, 'Metadata only.',
        {'type': 'object', 'properties': {'label': {'type': 'string', 'enum': ['original']}}},
        [role], False, 'builtin', {}, False)] for role in ('planner', 'worker')}
    limits = Limits(max_turns=2, max_tokens=200, max_seconds=3, max_tool_calls=1,
                    max_tool_output_bytes=256, max_request_bytes=4096)
    inputs = OperatorRuntimeInputs(executor=executor, providers=providers, transcript=transcript,
        prompts=prompts, graph_catalog=catalog, limits=limits, policy_revision='inert-policy-1',
        current=callbacks['current'], lifecycle_guard=callbacks['lifecycle_guard'],
        authorize=callbacks['authorize'], asset_authorize=callbacks['asset_authorize'],
        validate_input=callbacks['validate_input'], activity_authorize=None, cleanup_authorize=None)
    assert calls == [], calls
    return SimpleNamespace(inputs=inputs, executor=executor, tasks=tasks, agents=agents,
        profiles=profiles, factory=factory, providers=providers, store=store, transcript=transcript,
        prompts=prompts, catalog=catalog, limits=limits, callbacks=callbacks, calls=calls)


_ACTIVE = ContextVar('operator_runtime_acceptance_observation', default=None)
_FILESYSTEM_EVENTS = frozenset({'open', 'os.listdir', 'os.scandir', 'os.chdir', 'os.fchdir',
    'os.mkdir', 'os.rmdir', 'os.remove', 'os.rename', 'os.link', 'os.symlink', 'os.chmod',
    'os.chown', 'os.truncate', 'os.utime', 'os.putenv', 'os.unsetenv'})


def _observe_audit(event, args):
    active = _ACTIVE.get()
    if active is not None and (event in _FILESYSTEM_EVENTS or event.startswith(('socket.', 'subprocess.'))
                              or event in {'os.system', 'os.fork', 'os.forkpty', 'os.posix_spawn'}):
        active['effects'].append(event)
        raise UnexpectedEffect('Construction audit event: ' + event)


sys.addaudithook(_observe_audit)


def _method_codes(cls, names):
    return {getattr(cls, name).__code__ for name in names}


_FORBIDDEN_CODES = set().union(
    *(_method_codes(cls, ('__init__',)) for cls in
      (TaskStore, AgentStore, ProfileStore, ProfileFactory, TaskProviderResolver,
       TranscriptStore, ChatTranscriptAdapter, Provider)),
    _method_codes(ProfileFactory, ('validate', 'build')),
    _method_codes(TaskProviderResolver, ('select', 'bind', '_selection')),
    _method_codes(TaskRuntimeAssembly, ('bootstrap_configuration', 'engine_factory', '_select', '_goals')),
    _method_codes(BootstrapCoordinator, ('run', 'run_admitted', '_run')),
)


@contextmanager
def observe_construction(*, forbidden_codes=()):
    """Observe actual calls; do not replace constructors, callbacks or guards."""
    state = {'assembly_constructions': 0, 'effects': [], 'forbidden_calls': []}
    previous = sys.getprofile()
    forbidden = _FORBIDDEN_CODES | set(forbidden_codes)

    def profile(frame, event, arg):
        if event == 'call':
            if frame.f_code in forbidden:
                state['forbidden_calls'].append(frame.f_code.co_qualname)
                raise UnexpectedEffect('Forbidden construction call: ' + frame.f_code.co_qualname)
            if frame.f_code is TaskRuntimeAssembly.__init__.__code__:
                state['assembly_constructions'] += 1
        if previous is not None:
            previous(frame, event, arg)

    token = _ACTIVE.set(state)
    try:
        sys.setprofile(profile)
        yield state
    finally:
        sys.setprofile(previous)
        _ACTIVE.reset(token)


class OperatorRuntimeInertAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='artex-inert-composition-')
        self.addCleanup(self.temp.cleanup)
        self.fixture = make_fixture(Path(self.temp.name))

    def compose(self, inputs=None):
        fixture = self.fixture
        before = tuple(Path(self.temp.name).iterdir())
        previous_profile = sys.getprofile()
        with observe_construction() as observation:
            assembly = compose_operator_runtime(fixture.inputs if inputs is None else inputs)
            self.assertIs(type(assembly), TaskRuntimeAssembly)
            self.assertTrue(assembly.validate_bindings())
        self.assertIs(sys.getprofile(), previous_profile)
        self.assertEqual(observation, {'assembly_constructions': 1, 'effects': [], 'forbidden_calls': []})
        self.assertEqual(fixture.calls, [])
        self.assertEqual(tuple(Path(self.temp.name).iterdir()), before)
        self.assertFalse(fixture.store._runtime_started)
        self.assertIsNone(fixture.store._runtime_lease)
        self.assertEqual(fixture.factory._cache, {})
        return assembly

    def reject(self, inputs):
        previous_profile = sys.getprofile()
        with observe_construction() as observation:
            with self.assertRaises((TypeError, ValueError, RuntimeError)):
                compose_operator_runtime(inputs)
        self.assertIs(sys.getprofile(), previous_profile)
        self.assertEqual(observation['effects'], [])
        self.assertEqual(observation['forbidden_calls'], [])
        self.assertLessEqual(observation['assembly_constructions'], 1)
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_actual_resource_identity_and_one_inert_assembly(self):
        f = self.fixture
        assembly = self.compose()
        for attribute, original in [('executor', f.executor), ('providers', f.providers), ('transcript', f.transcript),
                                    ('_limits', f.limits)]:
            self.assertIs(getattr(assembly, attribute), original)
        self.assertEqual(assembly._pins, (f.executor, f.providers, f.transcript, f.tasks, f.agents, f.profiles, f.factory))
        self.assertIs(type(assembly.coordinator), BootstrapCoordinator)
        self.assertIs(type(assembly.coordinator.journal), BootstrapJournal)
        self.assertIs(assembly.coordinator.journal.executor, f.executor)
        self.assertEqual(assembly._configurations, {})
        self.assertIsNone(assembly._timed_admission)
        self.assertIsNone(assembly._timing_policy)
        self.assertIsNone(assembly._process_policy)

    def test_host_policy_callbacks_preserve_identity_without_invocation(self):
        f = self.fixture
        assembly = self.compose()
        for attribute, key in [('_current', 'current'), ('_guard', 'lifecycle_guard'), ('_authorize', 'authorize'),
                               ('_assets', 'asset_authorize'), ('_input', 'validate_input')]:
            self.assertIs(getattr(assembly, attribute), f.callbacks[key])
        self.assertIsNone(assembly._activity_authorize)
        self.assertIsNone(assembly._cleanup_authorize)

    def test_explicit_optional_callbacks_are_forwarded_not_called(self):
        f = self.fixture
        inputs = replace(f.inputs, activity_authorize=f.callbacks['activity_authorize'],
                         cleanup_authorize=f.callbacks['cleanup_authorize'])
        assembly = self.compose(inputs)
        self.assertIs(assembly._activity_authorize, inputs.activity_authorize)
        self.assertIs(assembly._cleanup_authorize, inputs.cleanup_authorize)

    def test_caller_prompts_and_nested_catalog_are_detached(self):
        f = self.fixture
        assembly = self.compose()
        f.prompts['goals'] = 'Changed after composition.'
        f.catalog['planner'][0].schema['properties']['label']['enum'].append('changed')
        f.catalog['worker'].clear()
        self.assertEqual(assembly._prompts['goals'], 'Inert goals fixture.')
        self.assertEqual(assembly._catalog['planner'][0].schema['properties']['label']['enum'], ['original'])
        self.assertEqual(len(assembly._catalog['worker']), 1)
        self.assertTrue(assembly.validate_bindings())
        self.assertEqual(f.calls, [])

    def test_all_fourteen_operator_inputs_are_required_keyword_only(self):
        names = {'executor', 'providers', 'transcript', 'prompts', 'graph_catalog', 'limits', 'policy_revision',
                 'current', 'lifecycle_guard', 'authorize', 'asset_authorize', 'validate_input',
                 'activity_authorize', 'cleanup_authorize'}
        self.assertEqual({field.name for field in fields(OperatorRuntimeInputs)}, names)
        for field in fields(OperatorRuntimeInputs):
            self.assertIs(field.default, MISSING)
            self.assertIs(field.default_factory, MISSING)
        self.assertTrue(all(p.kind is inspect.Parameter.KEYWORD_ONLY
                            for p in inspect.signature(OperatorRuntimeInputs).parameters.values()))
        for missing in sorted(names):
            values = {name: getattr(self.fixture.inputs, name) for name in names if name != missing}
            with self.subTest(missing=missing), self.assertRaises(TypeError):
                OperatorRuntimeInputs(**values)
        self.assertEqual(self.fixture.calls, [])

    def test_invalid_input_records_fail_without_effects(self):
        for value in [None, {}, object()]:
            with self.subTest(value=type(value).__name__):
                self.reject(value)

    def test_foreign_executor_identity_fails_without_queries(self):
        other = DenyExecutor(self.fixture.calls)
        self.reject(replace(self.fixture.inputs, executor=other))

    def test_missing_executor_callable_surface_fails_without_invocation(self):
        f = self.fixture
        f.executor.fetch_all = None
        self.reject(f.inputs)

    def test_resolver_resource_rebinding_is_rejected_without_grants(self):
        f = self.fixture
        f.agents.executor = DenyExecutor(f.calls)
        self.reject(f.inputs)

    def test_noncallable_required_policies_fail_before_effects(self):
        for name in ['current', 'lifecycle_guard', 'authorize', 'asset_authorize', 'validate_input']:
            for value in [None, False, 3]:
                with self.subTest(name=name, value=value):
                    self.reject(replace(self.fixture.inputs, **{name: value}))

    def test_invalid_optional_authority_and_limits_fail_before_effects(self):
        for name in ['activity_authorize', 'cleanup_authorize']:
            with self.subTest(name=name):
                self.reject(replace(self.fixture.inputs, **{name: False}))
        self.reject(replace(self.fixture.inputs, limits=None))

    def test_invalid_prompts_revision_and_role_keys_fail_without_effects(self):
        for prompts in [{'goals': 'Only one role.'}, {'goals': '', 'planner': 'p', 'worker': 'w'}]:
            with self.subTest(prompts=prompts):
                self.reject(replace(self.fixture.inputs, prompts=prompts))
        for revision in ['', 'x' * 129, None]:
            with self.subTest(revision=revision):
                self.reject(replace(self.fixture.inputs, policy_revision=revision))
        self.reject(replace(self.fixture.inputs, graph_catalog={'planner': []}))

    def test_instrumentation_rejects_effects_and_restores_profile(self):
        # Emit an inert audit event; do not attempt a filesystem operation.
        previous_profile = sys.getprofile()
        with self.assertRaises(UnexpectedEffect):
            with observe_construction() as audit_observation:
                sys.audit('open', str(Path(self.temp.name, 'must-not-exist')), 'r', 0)
        self.assertEqual(audit_observation['effects'], ['open'])
        self.assertIs(sys.getprofile(), previous_profile)
        self.assertFalse(Path(self.temp.name, 'must-not-exist').exists())

        def forbidden_probe():
            self.fail('Call observer failed before probe body')

        with self.assertRaises(UnexpectedEffect):
            with observe_construction(forbidden_codes={forbidden_probe.__code__}) as call_observation:
                forbidden_probe()
        self.assertEqual(len(call_observation['forbidden_calls']), 1)
        self.assertIs(sys.getprofile(), previous_profile)
        self.assertIsNone(_ACTIVE.get())


if __name__ == '__main__':
    unittest.main()
