"""Independent durable planner capability-identity regressions."""
import unittest
import test_planner_cycle as fixtures
from artex.agent.planner import PlannerCycle
from artex.agent.chat import ChatPersistenceError
from artex.agent.runtime import Tool
from artex.db.tool_catalog import CatalogTool


class PlannerToolsReview(unittest.TestCase):
    setUp=fixtures.PlannerTests.setUp

    def make_core(self,enabled=True,description='fixture capability'):
        tool=Tool('review_fixture',description,{'type':'object','properties':{}},
                  lambda context,args:'inert',lambda args:True)
        row=CatalogTool(tool.name,True,description,tool.input_schema,['planner'],enabled,'builtin',{},False)
        return PlannerCycle(store=self.graph,transcript=self.tx,current=self.core_base.current,
            lifecycle_guard=self.core_base.guard,provider_resolver=self.core_base.provider,
            authorize=self.core_base.authorize,asset_authorize=self.core_base.asset_authorize,
            additional_tools=[tool],additional_catalog=[row])

    def test_changed_tool_catalog_cannot_reuse_finalized_cycle(self):
        self.core_base=self.core
        first=self.make_core(True)
        self.provider.responses=[fixtures.text('old capability result')]
        wake=first.prepare(self.scope,'same-cycle');first.run(wake)
        changed=self.make_core(False)
        with self.assertRaises(ChatPersistenceError):changed.run(wake)
        self.assertEqual(len(self.provider.calls),1)

    def test_changed_tool_description_cannot_reuse_finalized_cycle(self):
        self.core_base=self.core
        first=self.make_core(True)
        self.provider.responses=[fixtures.text('old capability result')]
        wake=first.prepare(self.scope,'same-cycle');first.run(wake)
        changed=self.make_core(True,'different capability contract')
        with self.assertRaises(ChatPersistenceError):changed.run(wake)
        self.assertEqual(len(self.provider.calls),1)
