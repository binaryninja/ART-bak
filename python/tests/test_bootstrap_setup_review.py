"""Independent assembled-bootstrap binding review; inert inherited fixtures."""
import unittest
from artex.manager import RuntimeUnavailable
import test_bootstrap_setup as fixture
class Review(unittest.TestCase):
    def fixture(self):
        f=fixture.BootstrapSetupTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_configuration_cannot_rebind_coordinator_private_executor(self):
        for resource,attribute in (('coordinator','_executor'),('coordinator','_journal'),('journal','_executor')):
            with self.subTest(resource=resource,attribute=attribute):
                f=self.fixture()
                def changed(task):
                    target=f.f.coordinator if resource=='coordinator' else f.f.coordinator.journal
                    setattr(target,attribute,object());return f.configuration
                bundle=f.build(bootstrap_configuration=changed)
                with self.assertRaises(RuntimeUnavailable):bundle.facade.start('5')
                self.assertEqual(bundle.manager.status('5'),'created')
                self.assertEqual(bundle.runner.handles(),())
                self.assertIsNone(f.f.db.receipt)
    def test_callback_copy_does_not_change_loaded_lifecycle(self):
        f=self.fixture()
        def changed(task):task['id']='99';task['exploration_id']=101;return f.configuration
        bundle=f.build(bootstrap_configuration=changed);bundle.facade.start('5')
        handle=bundle.runner.handles()[0]
        self.assertEqual(handle.task_id,'5');self.assertEqual(handle.exploration_id,9)
        self.assertIsNone(f.f.db.receipt);self.assertEqual(f.f.provider.calls,[])
    def test_actual_shared_admission_reentrant_constructor_remains_closed(self):
        f=self.fixture();bundle=f.build()
        with bundle.admission.admission_guard('start','5'):
            with self.assertRaises(RuntimeUnavailable):bundle.facade.start('5')
        self.assertEqual(bundle.runner.handles(),());self.assertEqual(f.selections,[])
