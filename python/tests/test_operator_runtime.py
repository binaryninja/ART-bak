"""Actual inert composition, with effects denied rather than guards bypassed."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields, replace
import sys
import unittest

from artex.agent.operator_runtime import OperatorRuntimeInputs, compose_operator_runtime
from artex.agent.runtime import Limits
from artex.agent.task_providers import TaskProviderResolver, TaskProviderUnavailable
from artex.agent.task_runtime import TaskRuntimeAssembly
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.db.agents import AgentStore
from artex.db.profiles import ProfileStore
from artex.db.tasks import TaskStore
from artex.db.tool_catalog import CatalogTool
from artex.manager import RuntimeUnavailable
from artex.providers.factory import ProfileFactory
from artex.transcripts import TranscriptStore


_active_effects = None


def _audit(event, args):
    if _active_effects is not None and (
        event == 'open' or event.startswith(('socket.', 'subprocess.'))
        or event in ('os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.chdir',
                     'os.system', 'os.posix_spawn', 'os.exec', 'os.fork', 'os.scandir',
                     'os.listdir', 'os.truncate', 'os.link', 'os.symlink')
    ):
        _active_effects.append(event)
        raise AssertionError('External effect during inert composition')


sys.addaudithook(_audit)


@contextmanager
def no_external_effects():
    global _active_effects
    assert _active_effects is None
    events = []
    _active_effects = events
    try:
        yield events
    finally:
        _active_effects = None


class DeniedExecutor:
    def __init__(self, calls):
        self.calls = calls

    def denied(self, *args):
        self.calls.append('executor')
        raise AssertionError('No executor operation during composition')

    fetch_all = fetch_one = execute = transaction = denied


def fixture():
    calls = []

    def deny(name):
        def call(*args, **kwargs):
            calls.append(name)
            raise AssertionError('No callback invocation during composition')
        return call

    executor = DeniedExecutor(calls)
    factory = ProfileFactory(deny('transport_factory'), clock=deny('clock'), sleep=deny('sleep'))
    providers = TaskProviderResolver(
        TaskStore(executor), AgentStore(executor), ProfileStore(executor), factory,
        current=deny('provider_current'), lifecycle_guard=deny('provider_guard'),
        authorize_task=deny('task_authority'), authorize_profile=deny('profile_authority'),
        allow_global=False,
    )
    # Constructor stores this absolute string; no directory is created or opened.
    transcript = ChatTranscriptAdapter(
        TranscriptStore('/operator-composition-inert-fixture', max_bytes=4096),
        agent_id='operator-fixture', recovery_guard=deny('recovery_guard'),
    )
    row = CatalogTool('ListObjectives', True, 'Explicit metadata',
                      {'type': 'object', 'properties': {}}, ['planner'], True, 'builtin', {}, False)
    inputs = OperatorRuntimeInputs(
        executor=executor, providers=providers, transcript=transcript,
        prompts={'goals': 'Operator goals', 'planner': 'Operator planner', 'worker': 'Operator worker'},
        graph_catalog={'planner': [row], 'worker': []},
        limits=Limits(max_turns=2, max_tokens=100, max_seconds=3,
                      max_tool_calls=2, max_tool_output_bytes=1024, max_request_bytes=2048),
        policy_revision='operator-policy-1', current=deny('current'),
        lifecycle_guard=deny('lifecycle_guard'), authorize=deny('authorize'),
        asset_authorize=deny('asset_authorize'), validate_input=deny('validate_input'),
        activity_authorize=None, cleanup_authorize=None,
    )
    return inputs, calls, deny


class OperatorRuntimeTests(unittest.TestCase):
    def compose(self, inputs, calls):
        with no_external_effects() as events:
            assembly = compose_operator_runtime(inputs)
        self.assertEqual(events, [])
        self.assertEqual(calls, [])
        return assembly

    def test_exact_assembly_preserves_resources_callbacks_limits_and_empty_runtime_state(self):
        inputs, calls, _ = fixture()
        assembly = self.compose(inputs, calls)
        self.assertIs(type(assembly), TaskRuntimeAssembly)
        for name in ('executor', 'providers', 'transcript'):
            self.assertIs(getattr(assembly, name), getattr(inputs, name))
        for field, attr in [('current', '_current'), ('lifecycle_guard', '_guard'),
                            ('authorize', '_authorize'), ('asset_authorize', '_assets'),
                            ('validate_input', '_input'), ('limits', '_limits'),
                            ('policy_revision', '_revision')]:
            self.assertIs(getattr(assembly, attr), getattr(inputs, field))
        self.assertIs(assembly.coordinator.journal.executor, inputs.executor)
        self.assertIs(assembly.coordinator.guard, inputs.lifecycle_guard)
        self.assertEqual(assembly._configurations, {})
        for name in ('_timed_admission', '_timing_policy', '_process_policy',
                     '_activity_authorize', '_cleanup_authorize'):
            self.assertIsNone(getattr(assembly, name))
        self.assertFalse(inputs.transcript.store._runtime_started)
        self.assertIsNone(inputs.transcript.store._runtime_lease)

    def test_every_keyword_is_required_including_disabled_optional_policies(self):
        inputs, calls, _ = fixture()
        values = {f.name: getattr(inputs, f.name) for f in fields(inputs)}
        self.assertEqual(len(values), 14)
        for name in values:
            with self.subTest(omitted=name), self.assertRaises(TypeError):
                OperatorRuntimeInputs(**{k: v for k, v in values.items() if k != name})
        with self.assertRaises(TypeError):
            OperatorRuntimeInputs(*values.values())
        self.assertEqual(calls, [])

    def test_optional_callbacks_are_forwarded_by_identity_without_invocation(self):
        for activity, cleanup in ((True, False), (False, True), (True, True)):
            inputs, calls, deny = fixture()
            inputs = replace(inputs, activity_authorize=deny('activity') if activity else None,
                             cleanup_authorize=deny('cleanup') if cleanup else None)
            assembly = self.compose(inputs, calls)
            self.assertIs(assembly._activity_authorize, inputs.activity_authorize)
            self.assertIs(assembly._cleanup_authorize, inputs.cleanup_authorize)

    def test_shallow_record_and_existing_constructor_copy_semantics(self):
        inputs, calls, _ = fixture()
        with self.assertRaises(FrozenInstanceError):
            inputs.policy_revision = 'changed'
        inputs.prompts['goals'] = 'Explicit edit before composition'
        assembly = self.compose(inputs, calls)
        self.assertEqual(assembly._prompts['goals'], inputs.prompts['goals'])
        inputs.prompts['goals'] = 'Caller edit afterward'
        inputs.graph_catalog['planner'][0].schema['properties']['later'] = {'type': 'string'}
        inputs.graph_catalog['worker'].append(inputs.graph_catalog['planner'][0])
        self.assertEqual(assembly._prompts['goals'], 'Explicit edit before composition')
        self.assertEqual(assembly._catalog['planner'][0].schema['properties'], {})
        self.assertEqual(assembly._catalog['worker'], [])

    def test_input_repr_never_reads_resource_reprs_or_contains_metadata(self):
        inputs, calls, _ = fixture()
        class Private:
            def __repr__(self):
                raise AssertionError('Resource repr must not be read')
        represented = repr(replace(inputs, executor=Private()))
        self.assertNotIn('Operator goals', represented)
        self.assertNotIn('operator-policy-1', represented)
        self.assertIn('OperatorRuntimeInputs', represented)
        self.assertEqual(calls, [])

    def test_rejects_missing_executor_methods_without_calling_present_methods(self):
        for method in ('fetch_all', 'fetch_one', 'execute', 'transaction'):
            inputs, calls, _ = fixture()
            setattr(inputs.executor, method, None)
            with no_external_effects() as events, self.assertRaises(TypeError):
                compose_operator_runtime(inputs)
            self.assertEqual(calls, [])
            self.assertEqual(events, [])

    def test_rejects_wrong_record_and_resource_types_without_effects(self):
        inputs, calls, _ = fixture()
        for value in (None, {}, object()):
            with self.subTest(record=value), self.assertRaises(TypeError):
                compose_operator_runtime(value)
        for name in ('providers', 'transcript'):
            with self.subTest(field=name), no_external_effects(), self.assertRaises(TypeError):
                compose_operator_runtime(replace(inputs, **{name: object()}))
        self.assertEqual(calls, [])

    def test_rejects_noncallable_host_choices_and_missing_metadata(self):
        inputs, calls, _ = fixture()
        for name in ('current', 'lifecycle_guard', 'authorize', 'asset_authorize', 'validate_input',
                     'activity_authorize', 'cleanup_authorize'):
            with self.subTest(field=name), no_external_effects(), self.assertRaises(TypeError):
                compose_operator_runtime(replace(inputs, **{name: False}))
        for name in ('prompts', 'graph_catalog'):
            with self.subTest(field=name), no_external_effects(), self.assertRaises(TypeError):
                compose_operator_runtime(replace(inputs, **{name: None}))
        self.assertEqual(calls, [])

    def test_existing_constructor_rejects_invalid_revision_limits_roles_and_transcript(self):
        for changes in ({'policy_revision': ''}, {'policy_revision': 'x'*129}, {'limits': None},
                        {'prompts': {'goals': 'only one role'}},
                        {'prompts': {'goals': ' ', 'planner': 'p', 'worker': 'w'}},
                        {'graph_catalog': {'worker': []}}):
            inputs, calls, _ = fixture()
            with self.subTest(fields=tuple(changes)), no_external_effects(), self.assertRaises(ValueError):
                compose_operator_runtime(replace(inputs, **changes))
            self.assertEqual(calls, [])
        inputs, calls, _ = fixture()
        inputs.transcript.store = object()
        with no_external_effects(), self.assertRaises(ValueError):
            compose_operator_runtime(inputs)
        self.assertEqual(calls, [])

    def test_mismatched_executor_and_stale_resolver_are_not_rewrapped(self):
        inputs, calls, _ = fixture()
        with no_external_effects(), self.assertRaises(ValueError):
            compose_operator_runtime(replace(inputs, executor=DeniedExecutor(calls)))
        for change in ('tasks_executor', 'agents_executor', 'profiles_executor', 'factory', 'guard'):
            inputs, calls, deny = fixture()
            if change.endswith('_executor'):
                getattr(inputs.providers, change.split('_')[0]).executor = DeniedExecutor(calls)
            else:
                setattr(inputs.providers, change, object() if change == 'factory' else deny('replacement'))
            with self.subTest(change=change), no_external_effects(), self.assertRaises(TaskProviderUnavailable):
                compose_operator_runtime(inputs)
            self.assertEqual(calls, [])

    def test_returned_original_binding_checks_still_reject_policy_and_resource_drift(self):
        for change in ('executor', 'providers', '_authorize', '_limits'):
            inputs, calls, _ = fixture()
            assembly = self.compose(inputs, calls)
            setattr(assembly, change, object())
            with self.subTest(change=change), no_external_effects(), self.assertRaises(RuntimeUnavailable):
                assembly.validate_bindings()
            self.assertEqual(calls, [])

    def test_resolver_changes_during_existing_metadata_copy_are_rejected(self):
        for field in ('factory', 'current'):
            inputs, calls, deny = fixture()
            copied = []
            class Prompts(dict):
                def __deepcopy__(self, memo):
                    copied.append('prompts')
                    setattr(inputs.providers, field, object() if field == 'factory' else deny('changed_current'))
                    return dict(self)
            inputs = replace(inputs, prompts=Prompts(inputs.prompts))
            with self.subTest(field=field), no_external_effects(), self.assertRaises(TaskProviderUnavailable):
                compose_operator_runtime(inputs)
            self.assertEqual(copied, ['prompts'])
            self.assertEqual(calls, [])

    def test_existing_metadata_copy_failure_propagates_without_fallback(self):
        inputs, calls, _ = fixture()
        failure = LookupError('synthetic copy failure')
        copied = []
        class Prompts(dict):
            def __deepcopy__(self, memo):
                copied.append('prompts')
                raise failure
        with no_external_effects(), self.assertRaises(LookupError) as raised:
            compose_operator_runtime(replace(inputs, prompts=Prompts(inputs.prompts)))
        self.assertIs(raised.exception, failure)
        self.assertEqual(copied, ['prompts'])
        self.assertEqual(calls, [])

    def test_no_catalog_semantic_validator_or_discovery_is_introduced(self):
        inputs, calls, _ = fixture()
        # Existing constructor accepts serializable metadata; this is deliberately
        # not tool eligibility or readiness approval. The later owner validates it.
        row = replace(inputs.graph_catalog['planner'][0], key='not-an-installed-tool',
                      kind='operator-opaque', enabled=False)
        assembly = self.compose(replace(inputs, graph_catalog={'planner': [row], 'worker': []}), calls)
        self.assertEqual(assembly._catalog['planner'][0], row)


if __name__ == '__main__':
    unittest.main()
