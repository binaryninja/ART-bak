"""Independent initialization-mode construction fences, fake SQL/models only."""
import unittest
import test_engine_runner_bootstrap as fixture
from artex.manager import RuntimeUnavailable,TaskConflict

class Review(unittest.TestCase):
    setUp=fixture.EngineBootstrapTests.setUp
    def test_factory_cannot_rebind_coordinator_executor_before_install(self):
        original=self.runner._bootstrap_factory
        def changed(*args):
            h=original(*args);h.coordinator.journal.executor=object();return h
        self.runner._bootstrap_factory=changed
        with self.assertRaises(RuntimeUnavailable):self.f.host.start('5')
        self.assertEqual(self.runner.handles(),());self.assertEqual(self.f.provider.calls,[])
    def test_factory_cannot_change_pinned_configuration_before_install(self):
        original=self.runner._bootstrap_factory
        def changed(*args):
            h=original(*args);h.policy_revision='other';return h
        self.runner._bootstrap_factory=changed
        with self.assertRaises(RuntimeUnavailable):self.f.host.start('5')
        self.assertEqual(self.runner.handles(),());self.assertEqual(self.f.provider.calls,[])
    def test_partial_initialization_does_not_fall_through_to_initialized_mode(self):
        self.f.host.start('5');h=self.runner.handles()[0]
        self.f.advance(h);self.runner._initialization_readiness=lambda task:False
        self.runner._readiness=lambda task:h.scope
        with self.assertRaises(TaskConflict):self.runner.start(self.constructions[0],h.token)
        self.assertIs(self.runner.handles()[0],h);self.assertEqual(self.f.f.pp.calls,[])

if __name__=='__main__':unittest.main()
