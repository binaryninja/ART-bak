"""Independent planner durability and SQL atomicity review, inert fixtures only."""
from dataclasses import replace
import unittest
from artex.agent.runtime import Limits
from artex.agent.chat import ChatPersistenceError
import test_planner_cycle as fixtures
text=fixtures.text

class PlannerReviewTests(unittest.TestCase):
    setUp=fixtures.PlannerTests.setUp
    restart=fixtures.PlannerTests.restart
    def test_terminal_replay_restores_exact_result_after_restart(self):
        self.provider.responses=[text('durable planner answer')]
        wake=self.core.prepare(self.scope,'cycle')
        initial=self.core.run(wake);self.restart();replayed=self.core.run(wake)
        self.assertTrue(replayed.outcome.reused)
        self.assertIsNotNone(replayed.outcome.result)
        self.assertEqual(replayed.outcome.result.text,initial.outcome.result.text)
        self.assertEqual(len(self.provider.calls),1)

    def test_changed_limits_replay_rejected_without_second_provider(self):
        self.provider.responses=[text('first')]
        wake=self.core.prepare(self.scope,'cycle');self.core.run(wake);self.restart()
        self.core.limits=replace(self.core.limits,max_turns=self.core.limits.max_turns+1)
        with self.assertRaises(ChatPersistenceError):self.core.run(wake)
        self.assertEqual(len(self.provider.calls),1)

    def test_failed_lineage_rolls_back_created_node(self):
        original=self.db.execute
        def execute(query):
            if query.sql.startswith('INSERT INTO exploration_edges'):return 0
            return original(query)
        self.db.execute=execute
        result=self.core.run(self.core.prepare(self.scope,'cycle'))
        self.assertEqual(len(self.db.rows),2);self.assertFalse(result.active_intents)

    def test_parent_scope_query_covers_runtime_and_exact_exploration(self):
        self.core.run(self.core.prepare(self.scope,'cycle'))
        query=next(q for q in self.db.queries if q.sql.startswith('SELECT id FROM tasks WHERE id='))
        self.assertEqual(query.params,(5,9))
        for clause in ("status='running'",'NOT paused','NOT queued','deleted_at IS NULL','archived_at IS NULL','FOR UPDATE'):
            self.assertIn(clause,query.sql)
