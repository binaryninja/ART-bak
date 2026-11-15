"""Independent installed-generation capability tests; inert model and SQL fixtures."""
import unittest
from threading import Thread
import test_bootstrap_handle as fixtures
from artex.agent.bootstrap import BootstrapConflict

class Review(unittest.TestCase):
    setUp=fixtures.BootstrapHandleTests.setUp
    start=fixtures.BootstrapHandleTests.start
    advance=fixtures.BootstrapHandleTests.advance
    def test_retained_goal_capabilities_revoked_after_prepare(self):
        original=self.coordinator.factory;captured=[]
        def factory(scope,current,guard):
            captured.append((scope,current,guard));return original(scope,current,guard)
        self.coordinator.factory=factory
        h=self.start();self.advance(h)
        scope,current,guard=captured[0]
        with self.assertRaises(BootstrapConflict):current(scope)
        with self.assertRaises(BootstrapConflict):
            with guard(scope):self.fail('retained guard entered')
        self.assertFalse(h.settle_completed(self.manager.complete));self.assertEqual(self.manager.status('5'),'running')
    def test_goal_capability_rejects_other_thread_during_active_provider(self):
        original=self.coordinator.factory;captured=[];errors=[]
        def factory(scope,current,guard):captured.append((scope,current));return original(scope,current,guard)
        self.coordinator.factory=factory
        def hook():
            def foreign():
                try:captured[0][1](captured[0][0])
                except Exception as error:errors.append(error)
            thread=Thread(target=foreign);thread.start();thread.join(2)
            self.assertFalse(thread.is_alive())
        self.provider.hook=hook
        h=self.start();self.advance(h)
        self.assertTrue(errors);self.assertTrue(all(isinstance(e,BootstrapConflict) for e in errors))
    def test_retained_admitted_entry_cannot_restart_phase(self):
        h=self.start();self.advance(h);before=len(self.provider.calls)
        with self.assertRaises(BootstrapConflict):self.coordinator.run_admitted(h)
        self.assertEqual(len(self.provider.calls),before);self.assertEqual(h.phase,'engine')
    def test_child_factory_cannot_authorize_engine_current_during_construction(self):
        original=self.child_factory;values=[]
        def factory(task,token,scope,current,guard):
            values.append(current(scope,token));return original(task,token,scope,current,guard)
        self.child_factory=factory
        h=self.start();self.advance(h)
        self.assertEqual(values,[False]);self.assertEqual(self.f.pp.calls,[])

if __name__=='__main__':unittest.main()
