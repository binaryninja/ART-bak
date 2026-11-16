"""Inert regressions: no subprocess or external transport is invoked.

Direct component composition only. TaskRuntimeAssembly currently rejects Bash
catalog entries, so these tests do not demonstrate deployed task reachability.
"""
import tempfile
import unittest

from artex.agent.runtime import BoundedAgent, Grant
from artex.agent.worker import WorkerUnresolved
from artex.db.tool_catalog import CatalogTool
from artex.tools.process import BashTool, CleanupUncertain
from artex.tools.registry import ToolRegistry
from test_agent_runtime import FakeProvider, assistant, request
import test_worker_cycle as worker_fixture


class ProcessUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.launches = []
        def runner(context, launch):
            self.launches.append(launch)
            raise CleanupUncertain('inert drain uncertainty')
        self.tool = BashTool(self.tmp.name, shell='/bin/bash', environment={},
            gate=lambda *args: Grant(True, True, True), runner=runner)

    def test_bounded_agent_does_not_reduce_cleanup_uncertainty_to_result(self):
        provider = FakeProvider(assistant('', [('call', 'Bash', {'command': 'inert fixture'})]))
        agent = BoundedAgent(provider, gate=lambda *args: Grant(True, True, True),
                             tools=self.tool.tools())
        with self.assertRaises(CleanupUncertain):
            agent.run(request())
        self.assertEqual(len(self.launches), 1)

    def test_direct_worker_does_not_terminally_settle_undrained_effect(self):
        fixture = worker_fixture.WorkerCycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        def registry(scope, record):
            tool = self.tool.tools()[0]
            reg = ToolRegistry('worker', handlers=[tool],
                current=lambda revision: fixture.valid,
                lifecycle_guard=lambda revision: fixture.guard(),
                authorize=lambda *args: Grant(True, True, True))
            reg.reload([CatalogTool(tool.name, True, tool.description, tool.input_schema,
                        ['worker'], True, 'builtin', {}, False)], scope.revision)
            return reg
        fixture.registry = registry
        fixture.worker = fixture.new_worker()
        fixture.provider.responses = [({'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'a', 'name': 'Bash',
             'input': {'command': 'inert fixture'}}]}, 'tool_use', {})]
        with self.assertRaises(WorkerUnresolved):
            fixture.worker.run(fixture.scope, 'inert-client')
        self.assertEqual(fixture.db.rows[0][4], 'running')
        self.assertEqual(fixture.db.rows[0][10], 4)
        self.assertEqual(len(self.launches), 1)


if __name__ == '__main__':
    unittest.main()
