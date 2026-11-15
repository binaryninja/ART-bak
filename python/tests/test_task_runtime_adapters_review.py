"""Independent lifecycle adapter regressions; inert streams, no providers/network."""
import unittest
from types import SimpleNamespace
from test_task_providers import Tests as ProviderFixture,Context
from artex.agent.runtime import RunStopped
from artex.agent.task_providers import TaskProviderUnavailable

class AdapterReview(unittest.TestCase):
    def fixture(self):
        f=ProviderFixture();f.setUp();return f
    def test_stream_cancellation_before_resume_does_not_advance_transport(self):
        f=self.fixture();binding=f.binding();context=Context();steps=[];closed=[]
        class Stream:
            def __iter__(self):return self
            def __next__(self):steps.append(1);return {'type':'inert'}
            def close(self):closed.append(1)
        f.factory.build=lambda profile:SimpleNamespace(stream=lambda *a:Stream())
        iterator=binding.provider.stream(context,{})
        next(iterator);context.cancelled=True
        with self.assertRaises(RunStopped):next(iterator)
        self.assertEqual(steps,[1]);self.assertEqual(closed,[1])
    def test_stream_profile_change_before_resume_blocks_transport_advance(self):
        from dataclasses import replace
        f=self.fixture();binding=f.binding();steps=[]
        class Stream:
            def __iter__(self):return self
            def __next__(self):steps.append(1);return {'type':'inert'}
            def close(self):pass
        f.factory.build=lambda profile:SimpleNamespace(stream=lambda *a:Stream())
        iterator=binding.provider.stream(Context(),{});next(iterator)
        f.rows[2]=replace(f.rows[2],model='changed')
        with self.assertRaises(TaskProviderUnavailable):next(iterator)
        self.assertEqual(steps,[1])
    def test_bound_configuration_policy_swap_blocks_credential_read(self):
        f=self.fixture();binding=f.binding();f.reads.clear()
        f.resolver.authorize_profile=lambda *a:True
        with self.assertRaises(TaskProviderUnavailable):binding.provider.complete(Context(),{})
        self.assertEqual(f.reads,[]);self.assertEqual(f.calls,[])
    def test_early_stream_close_closes_inert_transport_once(self):
        f=self.fixture();binding=f.binding();closed=[]
        class Stream:
            def __iter__(self):return self
            def __next__(self):return {'type':'inert'}
            def close(self):closed.append(1)
        f.factory.build=lambda profile:SimpleNamespace(stream=lambda *a:Stream())
        iterator=binding.provider.stream(Context(),{});next(iterator);iterator.close()
        self.assertEqual(closed,[1])
    def test_bound_scope_mutation_does_not_construct_transport(self):
        f=self.fixture();binding=f.binding()
        # Replace the object, rather than mutating frozen source scope.
        binding.provider.scope=SimpleNamespace(task_id=5,exploration_id=9,run_id=71,revision='r1')
        with self.assertRaises(TaskProviderUnavailable):binding.provider.complete(Context(),{})
        self.assertEqual(f.construct,[]);self.assertEqual(f.calls,[])

class AssemblyReview(unittest.TestCase):
    def fixture(self):
        from test_task_runtime import Tests
        f=Tests();f.setUp();self.addCleanup(f.tmp.cleanup);return f
    def engine(self,f):
        from artex.agent.engine import EngineScope
        from artex.manager import CancellationToken
        from contextlib import nullcontext
        conf=f.assembly.bootstrap_configuration(f.p.snapshot)
        return f.assembly.engine_factory(f.p.snapshot,CancellationToken(),EngineScope(5,9,71,conf.policy_revision),lambda *a:True,lambda *a:nullcontext())
    def test_persisted_profile_drift_invalidates_planner_graph_authority(self):
        from dataclasses import replace
        f=self.fixture();handle=self.engine(f)
        f.p.rows[2]=replace(f.p.rows[2],model='changed-after-construction')
        self.assertIsNot(handle.planner.current(handle.planner_scope),True)
    def test_transcript_store_rebind_rejected_before_runtime_authority(self):
        from artex.transcripts import TranscriptStore
        from artex.manager import RuntimeUnavailable
        import tempfile
        f=self.fixture();self.engine(f)
        with tempfile.TemporaryDirectory() as root:
            f.transcript.store=TranscriptStore(root)
            with self.assertRaises(RuntimeUnavailable):f.assembly.validate_bindings()
    def with_current(self,f,callback):
        from artex.agent.task_runtime import TaskRuntimeAssembly
        old=f.assembly
        f.assembly=TaskRuntimeAssembly(f.p.db,providers=f.p.resolver,transcript=f.transcript,prompts=old._prompts,policy_revision='policy-1',current=callback,lifecycle_guard=old._guard,authorize=old._authorize,asset_authorize=old._assets,validate_input=old._input,graph_catalog=old._catalog)
    def test_host_current_revocation_blocks_existing_engine(self):
        f=self.fixture();live=[True];self.with_current(f,lambda *a:live[0]);handle=self.engine(f)
        live[0]=False
        self.assertIsNot(handle.planner.current(handle.planner_scope),True)
    def test_last_host_current_callback_cannot_rebind_transcript(self):
        f=self.fixture();calls=[0]
        def current(*a):
            calls[0]+=1
            if calls[0]==2:f.transcript.agent_id='foreign-agent'
            return True
        self.with_current(f,current);handle=self.engine(f);calls[0]=0
        from artex.manager import RuntimeUnavailable
        with self.assertRaises(RuntimeUnavailable):handle.planner.current(handle.planner_scope)
