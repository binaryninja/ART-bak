"""Owning store identity cannot be rebound by host callbacks; no live providers."""
import unittest
from artex.agent.chat import ChatBinding,ChatUnavailable
from artex.db.workers import WorkerConflict,WorkerStore
from artex.db.exploration import ExplorationStore
import test_planner_cycle as planner_fixture
import test_worker_cycle as worker_fixture

class CycleResourceBindingTests(unittest.TestCase):
    def planner(self):
        f=planner_fixture.PlannerTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def worker(self):
        f=worker_fixture.WorkerCycleTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_worker_current_cannot_replace_executor(self):
        f=self.worker();foreign=worker_fixture.Database()
        def current(*args):f.worker.store.executor=foreign;return True
        f.worker.current=current
        with self.assertRaises(WorkerConflict):f.worker.run(f.scope,'foreign')
        self.assertEqual(foreign.queries,[]);self.assertEqual(f.provider.calls,[])
    def test_worker_provider_cannot_replace_store(self):
        f=self.worker();foreign=worker_fixture.Database()
        def provider(*args):
            f.worker.store=WorkerStore(foreign,1,10,lifetime_guard=f.guard)
            return ChatBinding(f.scope.model,f.provider)
        f.worker.provider=provider
        with self.assertRaises(WorkerConflict):f.worker.run(f.scope,'foreign')
        self.assertEqual(foreign.queries,[]);self.assertEqual(f.provider.calls,[])
    def test_planner_current_cannot_replace_executor(self):
        f=self.planner();foreign=planner_fixture.GraphDB()
        def current(*args):f.graph.executor=foreign;return True
        f.core.current=current
        with self.assertRaises(ChatUnavailable):f.core.prepare(f.scope,'foreign')
        self.assertEqual(foreign.queries,[]);self.assertEqual(f.provider.calls,[])
    def test_planner_provider_cannot_replace_store(self):
        f=self.planner();foreign=planner_fixture.GraphDB();wake=f.core.prepare(f.scope,'foreign')
        def provider(*args):
            f.core.store=ExplorationStore(foreign,9)
            return ChatBinding(f.scope.model,f.provider)
        f.core.provider=provider
        with self.assertRaises(Exception):f.core.run(wake)
        self.assertEqual(foreign.queries,[]);self.assertEqual(f.provider.calls,[])
