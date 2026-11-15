import unittest
from artex.agent import (TaskClock, RunInfo, RunContext, RunTrace, Constraint,
                         clamp_max_duration, constraint_block,
                         seed_unlock_from_history, terminal_reason_hint)


class AgentCoreTests(unittest.TestCase):
    def test_deadline_vectors(self):
        for deadline, budget, expected in [
            (0, 0, (0, False)), (0, 20, (20, False)),
            (110, 0, (10, True)), (110, 20, (10, True)),
            (110, 5, (5, False)), (110, 10, (10, True)),
            (90, 0, (1, True)), (90, .5, (.5, False)),
        ]:
            with self.subTest(deadline=deadline, budget=budget):
                self.assertEqual(clamp_max_duration(deadline, budget, now=lambda: 100), expected)

    def test_invalid_budget(self):
        for value in [float('inf'), float('nan')]:
            with self.assertRaises(ValueError):
                clamp_max_duration(1, value)

    def test_context_isolation(self):
        base = RunContext()
        run = base.with_task_clock(TaskClock(100, True)).with_run_info(RunInfo(task_id=2))
        self.assertEqual(base, RunContext())
        self.assertEqual(run.run_info.task_id, 2)
        self.assertTrue(run.task_clock.final)

    def test_constraints(self):
        self.assertEqual(constraint_block([]), '')
        result = constraint_block([Constraint('allow', '  local fixtures '),
                                   Constraint('invalid', 'network'), Constraint('deny', ' ')])
        self.assertIn('Allowed operations:\n- local fixtures', result)
        self.assertIn('Prohibited operations:\n- network', result)
        self.assertIn('does not grant authorization', result)

    def test_constraint_errors_fail_closed(self):
        def broken():
            yield Constraint('deny', 'network')
            raise RuntimeError('store unavailable')
        with self.assertRaises(RuntimeError):
            constraint_block(broken())

    def test_history(self):
        found = []
        uses = [{'name': 'Skill', 'input': value} for value in
                ['{"name":"offline"}', '{', 'null', '[]', '{"name":3}', '{"name":""}']]
        uses.append({'name': 'Other', 'input': '{"name":"no"}'})
        seed_unlock_from_history([{'tool_uses': uses}], found.append)
        self.assertEqual(found, ['offline'])
        seed_unlock_from_history(None, None)

    def test_unlock_failure_propagates(self):
        def reject(name):
            raise PermissionError(name)
        with self.assertRaises(PermissionError):
            seed_unlock_from_history([{'tool_uses': [{'name': 'Skill', 'input': '{"name":"x"}'}]}], reject)

    def test_trace_wrong_completion(self):
        trace = RunTrace()
        trace.start('a', 'offline', '{}', now=lambda: 5)
        trace.start('b', 'offline', '{}', now=lambda: 6)
        trace.done('a')
        self.assertTrue(trace.pending)
        trace.done('b')
        self.assertFalse(trace.pending)
        self.assertEqual(trace.at, 6)

    def test_unknown_terminal(self):
        self.assertIn('Unknown', terminal_reason_hint('new_reason'))
        self.assertIn('canceled', terminal_reason_hint(''))
        self.assertNotIn('Unknown', terminal_reason_hint('max_turns'))


if __name__ == '__main__':
    unittest.main()
