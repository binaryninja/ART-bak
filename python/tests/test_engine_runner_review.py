"""Independent exact manager/host/engine integration; inert SQL/providers only."""
import unittest
from artex.agent.engine_runner import EngineRunner
from artex.agent.engine import EngineScope
from artex.agent.host import AgentHost
from artex.manager import TaskManager,RuntimeUnavailable,CancellationToken
import test_engine as fixtures
import test_manager as manager_fixtures
import test_hosted_setup as hosted_fixtures
from artex.db.tasks import TaskStore

class SharedDB(hosted_fixtures.DB):
    def fetch_one(self,q):
        return fixtures.DB.fetch_one(self,q)
    def execute(self,q):
        if q.sql.startswith("UPDATE tasks SET status=$1, completed_at"):
            self.calls.append(q);row=list(self.row);row[7]=q.params[0];self.row=tuple(row);return 1
        return fixtures.DB.execute(self,q)

class EngineRunnerReview(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.db=SharedDB();row=list(self.db.row);row[7]='created';row[8]=False;self.db.row=tuple(row)
        self.f.db=self.db;self.f.graph.executor=self.db;self.f.worker.store.executor=self.db
        self.repository=TaskStore(self.db)
        def factory(task,token,scope):
            self.f.token=token
            return self.f.make()
        self.runner=EngineRunner(readiness=lambda task:EngineScope(5,9,1,'r1'),factory=factory)
        self.manager=TaskManager(self.repository,runner=self.runner,dto_enricher=manager_fixtures.enrich)
        self.manager.load_existing();self.host=AgentHost(self.manager,self.runner)
    def test_actual_manager_string_id_starts_then_engine_settles_exact_generation(self):
        self.assertTrue(self.host.start('5'))
        handle=self.runner.handles()[0]
        self.assertEqual(self.host.poll().pending,1)
        self.assertEqual(self.manager.status('5'),'running')
        self.assertEqual(self.runner.advance('5',handle.token,0).action,'planner')
        self.assertEqual(self.runner.advance('5',handle.token,1).action,'worker')
        self.assertEqual(self.host.poll().settled,())
        self.f.met()
        self.assertEqual(self.host.poll().settled,('5',))
        self.assertEqual(self.manager.status('5'),'done')
        self.assertFalse(handle.token.cancelled)
    def test_unsupported_create_does_not_leave_task_record(self):
        before=len(self.db.calls)
        with self.assertRaises(RuntimeUnavailable):self.host.create_task('fixture','goal')
        self.assertFalse(any(q.sql.startswith('INSERT') for q in self.db.calls[before:]))
        self.assertEqual(self.runner.handles(),())
    def test_actual_host_pause_removes_clean_engine_without_completion(self):
        self.host.start('5');handle=self.runner.handles()[0]
        result=self.host.pause('5')
        self.assertTrue(result['paused']);self.assertTrue(handle.token.cancelled)
        self.assertEqual(self.host.poll().settled,())
        self.assertEqual(self.runner.handles(),())
        self.assertNotEqual(self.db.row[7],'done')
    def test_approval_cannot_supply_foreign_task_scope(self):
        self.runner._readiness=lambda row:EngineScope(6,9,1,'r1')
        with self.assertRaises(RuntimeUnavailable):self.host.start('5')
        self.assertEqual(self.db.row[7],'created')
        self.assertEqual(self.f.pp.calls,[])
