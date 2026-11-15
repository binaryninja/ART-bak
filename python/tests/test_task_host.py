import unittest
from contextlib import contextmanager
from artex.agent.host import AgentHost
from artex.server.task_host import TaskHTTPManagerFacade
from artex.manager import TaskManager,RuntimeUnavailable
from test_manager import Repository,enrich
from test_agent_runner import make_runner
class Lifecycle:
    def __init__(self,runner):self.inner=runner
    def validate_create(self,*a,**k):return self.inner.validate_create(*a,**k)
    def ready(self,*a,**k):return self.inner.ready(*a,**k)
    def start(self,*a,**k):return self.inner.start(*a,**k)
    def handles(self):return self.inner.handles()
    def settle_completed(self,*a):return self.inner.settle_completed(*a)
class HostFacadeTests(unittest.TestCase):
    def setup_host(self,runner=None):
        runner=runner or Lifecycle(make_runner());manager=TaskManager(Repository(),runner=runner,dto_enricher=enrich);manager.load_existing()
        self.host=AgentHost(manager,runner);return self.host
    def test_protocol_not_concrete_runner_compatible(self):
        host=self.setup_host();self.assertTrue(host.start('1'));self.assertTrue(host.drain(2).all_settled)
    def test_missing_protocol_and_mismatched_identity_preflight(self):
        host=self.setup_host()
        with self.assertRaises(RuntimeUnavailable):AgentHost(host.manager,Lifecycle(make_runner()))
        host.runner.handles=None
        with self.assertRaises(RuntimeUnavailable):AgentHost(host.manager,host.runner)
    def test_missing_capacity_no_admission_and_metadata_whitelist(self):
        host=self.setup_host();facade=TaskHTTPManagerFacade(host);calls=[]
        host.create_task=lambda *a,**k:calls.append('create')
        with self.assertRaises(RuntimeUnavailable):facade.create_task('d','g')
        with self.assertRaises(RuntimeUnavailable):facade.start('1')
        with self.assertRaises(RuntimeUnavailable):facade.control_task('1','resume')
        with self.assertRaises(ValueError):facade.update_metadata('1',status='done')
        self.assertEqual(calls,[]);self.assertFalse(hasattr(facade,'complete'));self.assertFalse(hasattr(facade,'start_queued'))
    def test_policy_held_through_host_start_poll_fence(self):
        host=self.setup_host();events=[]
        @contextmanager
        def guard(operation,id):
            events.append(('enter',operation,id))
            try:yield True
            finally:events.append(('exit',operation,id))
        original=host.manager.start
        def start(*a,**k):
            self.assertEqual(len(events),1);self.assertTrue(host.poll().busy)
            return original(*a,**k)
        host.manager.start=start
        facade=TaskHTTPManagerFacade(host,admission_guard=guard)
        self.assertTrue(facade.start('1'));self.assertEqual(events,[('enter','start','1'),('exit','start','1')]);self.assertTrue(host.drain(2).all_settled)
    def test_denied_policy_and_pause_needs_no_capacity(self):
        host=self.setup_host();calls=[]
        @contextmanager
        def guard(*args):yield 1
        facade=TaskHTTPManagerFacade(host,admission_guard=guard)
        with self.assertRaises(RuntimeUnavailable):facade.start('1')
        host.control_task=lambda *a:calls.append(a) or {'paused':True}
        self.assertEqual(facade.control_task('1','pause'),{'paused':True});self.assertEqual(calls,[('1','pause')])
    def test_inventory_contract_and_changed_identity_fail_closed(self):
        host=self.setup_host();facade=TaskHTTPManagerFacade(host)
        host.runner.handles=lambda:[object()]
        with self.assertRaises(RuntimeUnavailable):host.poll()
        host.manager.runner=Lifecycle(make_runner())
        with self.assertRaises(RuntimeUnavailable):facade.task('1')
