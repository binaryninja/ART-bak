"""Actual manager, bootstrap journal, goals and engine; inert providers only."""
import unittest
from artex.agent.engine_runner import EngineRunner
from artex.agent.host import AgentHost
from artex.db.tasks import TaskStore,TaskCreateOptions
from artex.manager import TaskManager,RuntimeUnavailable,CancellationToken
import test_bootstrap_handle as fixture
import test_manager

class EngineBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.BootstrapHandleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.preflight=[];self.constructions=[]
        old_factory=self.f.runner.factory
        def ready(task):self.preflight.append(task);return True
        def factory(task,token,manager):
            self.assertIs(manager,self.f.manager);self.constructions.append(task)
            return old_factory(task,token)
        self.runner=EngineRunner(readiness=lambda task:self.fail('initialized readiness'),factory=lambda *a:self.fail('initialized factory'),
            initialization_readiness=ready,bootstrap_factory=factory)
        self.f.manager=TaskManager(TaskStore(self.f.db),runner=self.runner,dto_enricher=test_manager.enrich)
        self.f.manager.load_existing();self.f.host=AgentHost(self.f.manager,self.runner)
    def test_real_owned_path_allocates_only_on_first_advance(self):
        self.f.host.start('5');h=self.runner.handles()[0]
        self.assertIsNone(h.scope);self.assertIsNone(self.f.db.receipt);self.assertEqual(self.f.provider.calls,[])
        self.assertIs(self.runner.start(self.constructions[0],h.token),h)
        self.assertEqual(self.f.advance(h).action,'bootstrap_prepared');self.assertEqual(h.scope.run_id,71)
        self.assertIs(self.runner.handles()[0],h);self.assertEqual(self.f.host.poll().settled,())
        self.assertEqual(self.f.advance(h,1).action,'planner');self.assertEqual(self.f.advance(h,2).action,'worker')
        self.f.f.met();self.assertEqual(self.f.host.poll().settled,('5',));self.assertEqual(self.f.manager.status('5'),'done')
    def test_provisional_readiness_is_inert(self):
        self.runner.validate_create('desc','goal',TaskCreateOptions())
        self.assertTrue(self.runner.ready({'provisional':True,'description':'desc','goal':'goal','options':{}}))
        self.assertEqual(self.constructions,[]);self.assertIsNone(self.f.db.receipt);self.assertEqual(self.f.provider.calls,[])
    def test_unsupported_options_reject_before_preflight_or_admission(self):
        for options in ({'timeout_seconds':1},{'source_task_ids':[4]},{'intercept_rules':[{}]}):
            with self.subTest(options=options),self.assertRaises(RuntimeUnavailable):self.runner.validate_create('d','g',options)
        with self.assertRaises(RuntimeUnavailable):self.f.host.start('5',seed_first_intent=True)
        self.assertEqual(self.preflight,[]);self.assertEqual(self.constructions,[]);self.assertEqual(self.f.manager.status('5'),'created')
    def test_claimed_host_required(self):
        runner=EngineRunner(readiness=lambda t:None,factory=lambda *a:None,initialization_readiness=lambda t:True,bootstrap_factory=lambda *a:None)
        with self.assertRaises(RuntimeUnavailable):runner.validate_create('d','g',{})
    def test_nonliteral_readiness_rejects(self):
        self.runner._initialization_readiness=lambda task:1
        with self.assertRaises(RuntimeUnavailable):self.f.host.start('5')
        self.assertEqual(self.constructions,[]);self.assertEqual(self.f.manager.status('5'),'created')
    def test_cancel_before_tick_never_allocates(self):
        self.f.host.start('5');h=self.runner.handles()[0];self.f.host.pause('5')
        self.assertTrue(h.token.cancelled);self.assertIsNone(self.f.db.receipt);self.assertEqual(self.f.provider.calls,[])
        self.f.host.poll();self.assertEqual(self.runner.handles(),())
    def test_bootstrap_factory_foreign_database_rejected(self):
        original=self.runner._bootstrap_factory
        def foreign(*args):
            h=original(*args);h._executor=object();return h
        self.runner._bootstrap_factory=foreign
        with self.assertRaises(RuntimeUnavailable):self.f.host.start('5')
        self.assertEqual(self.runner.handles(),());self.assertIsNone(self.f.db.receipt)
    def test_capacity_preflight_includes_provisional(self):
        self.runner.max_pending=1;self.f.host.start('5')
        with self.assertRaises(RuntimeUnavailable):self.runner.ready({'provisional':True,'options':{}})
        self.assertEqual(len(self.constructions),1)
