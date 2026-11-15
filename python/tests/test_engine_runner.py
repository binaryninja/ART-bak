import unittest
from artex.agent.engine_runner import EngineRunner
from artex.manager import RuntimeUnavailable,TaskConflict,CancellationToken
from artex.agent.engine import EngineScope
import test_engine as fixture

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.EngineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.calls=[]
        self.task={'id':'5','exploration_id':9,'timeout_seconds':0}
        def factory(task,token,scope):
            self.calls.append((task,token,scope));return self.f.make()
        self.runner=EngineRunner(readiness=lambda task:EngineScope(5,9,1,'r1'),factory=factory)
    def start(self):return self.runner.start(self.task,self.f.token)
    def test_start_is_inert_exact_idempotent(self):
        self.assertTrue(self.runner.ready(self.task));h=self.start()
        self.assertIs(self.start(),h);self.assertEqual(len(self.calls),1)
        self.assertEqual(self.f.db.queries,[])
        self.assertEqual(self.runner.handles(),(h,))
    def test_creation_bootstrap_and_deadline_reject_before_factory(self):
        with self.assertRaises(RuntimeUnavailable):self.runner.validate_create('d','g',{})
        for field,value in [('seed_first_intent',True),('queue_mode','bootstrap'),('timeout_seconds',1),('exploration_id',0)]:
            with self.subTest(field=field),self.assertRaises(RuntimeUnavailable):
                self.runner.ready(dict(self.task,**{field:value}))
        self.assertEqual(self.calls,[])
    def test_scope_and_factory_identity_reject(self):
        self.runner._readiness=lambda t:EngineScope(6,9,1,'r1')
        with self.assertRaises(RuntimeUnavailable):self.start()
        self.runner._readiness=lambda t:EngineScope(5,9,1,'r1')
        with self.assertRaises(RuntimeUnavailable):self.runner.start(self.task,CancellationToken())
        self.assertEqual(self.runner.handles(),())
    def test_idle_is_not_done_persisted_goals_settle_exact_token(self):
        h=self.start();completed=[]
        self.assertTrue(h.wait(0))
        self.assertEqual(self.runner.settle_completed(lambda *a:completed.append(a) or True),())
        self.assertEqual(completed,[]);self.assertEqual(self.runner.handles(),(h,))
        self.f.met()
        self.assertEqual(self.runner.settle_completed(lambda *a:completed.append(a) or True),('5',))
        self.assertEqual(completed,[('5',self.f.token,'done')]);self.assertEqual(self.runner.handles(),())
    def test_explicit_advance_runs_real_child_and_wrong_token_rejects(self):
        self.start()
        with self.assertRaises(TaskConflict):self.runner.advance('5',CancellationToken(),0)
        self.assertEqual(self.runner.advance('5',self.f.token,0).action,'planner')
        self.assertEqual(self.runner.advance('5',self.f.token,1).action,'worker')
    def test_cancelled_clean_drain_retires_without_complete(self):
        h=self.start();h.cancel();self.assertEqual(self.runner.settle_completed(lambda *a:self.fail('must not complete')),())
        self.assertEqual(self.runner.handles(),())
    def test_unresolved_cancelled_is_not_discarded(self):
        h=self.start();h._unresolved=True;h.cancel()
        self.assertEqual(self.runner.settle_completed(lambda *a:self.fail('must not complete')),())
        self.assertEqual(self.runner.handles(),(h,))
        with self.assertRaises(TaskConflict):self.runner.start(self.task,CancellationToken())
    def test_completion_uncertainty_retains_handle(self):
        h=self.start();self.f.met()
        def fail(*a):raise RuntimeError('fixture')
        with self.assertRaises(Exception):self.runner.settle_completed(fail)
        self.assertTrue(h.unresolved);self.assertEqual(self.runner.handles(),(h,))

if __name__=='__main__':unittest.main()
