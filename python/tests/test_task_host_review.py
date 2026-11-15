"""Independent inert lifecycle protocol/facade review."""
import unittest
from contextlib import contextmanager
from artex.agent.host import AgentHost
from artex.server.task_host import TaskHTTPManagerFacade
from artex.manager import TaskManager,RuntimeUnavailable
from test_task_host import Lifecycle
from test_agent_runner import make_runner
from test_manager import Repository,enrich

class HostReview(unittest.TestCase):
    def host(self):
        runner=Lifecycle(make_runner());manager=TaskManager(Repository(),runner=runner,dto_enricher=enrich)
        manager.load_existing();return AgentHost(manager,runner)
    def test_invalid_settlement_receipts_cannot_report_all_settled(self):
        for invalid in ('done',None,[None],['same','same'],[object()]):
            with self.subTest(invalid=repr(invalid)):
                host=self.host();host.runner.settle_completed=lambda complete:invalid
                with self.assertRaises(RuntimeUnavailable):host.poll()
    def test_wait_requires_literal_boolean(self):
        host=self.host()
        class Handle:
            def cancel(self):pass
            def wait(self,timeout):return 1
        host.runner.handles=lambda:[Handle()]
        with self.assertRaises(RuntimeUnavailable):host.poll()
    def test_identity_changed_by_inventory_callback_not_reported_drained(self):
        host=self.host()
        def handles():
            host.manager.runner=Lifecycle(make_runner())
            return []
        host.runner.handles=handles
        with self.assertRaises(RuntimeUnavailable):host.poll()
    def test_capacity_identity_rechecked_after_guard_entry(self):
        host=self.host();calls=[]
        host.start=lambda *a,**k:calls.append('started')
        @contextmanager
        def guard(*args):
            host.manager.runner=Lifecycle(make_runner())
            yield True
        facade=TaskHTTPManagerFacade(host,admission_guard=guard)
        with self.assertRaises(RuntimeUnavailable):facade.start('1')
        self.assertFalse(calls)
    def test_all_metadata_mutations_outside_allowlist_fail(self):
        host=self.host();calls=[];host.manager.update_metadata=lambda *a,**k:calls.append(k)
        facade=TaskHTTPManagerFacade(host)
        for patch in ({'category':'x'},{'status':'done'},{'name':'x','run_id':2},{}):
            with self.assertRaises(ValueError):facade.update_metadata('1',**patch)
        self.assertFalse(calls)

if __name__=='__main__':unittest.main()
