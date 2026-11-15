import json,unittest
from dataclasses import replace
import test_planner_cycle as fixtures
from artex.agent.planner import PlannerCycle
from artex.agent.runtime import Grant
from artex.db.tool_catalog import CatalogTool
from artex.tools.graph import GraphTools,GraphScope

class PlannerExtraToolsTests(unittest.TestCase):
    setUp=fixtures.PlannerTests.setUp
    def extra(self,*,enabled=True,grant=None):
        bridge=GraphTools(self.graph,GraphScope(5,9,1,'r1','planner'),current=lambda scope:self.valid,lifecycle_guard=lambda scope:self.core.guard(self.scope),authorize=lambda *a:Grant(True,True,True),asset_authorize=lambda *a:Grant(True,True,True))
        tools=bridge.tools();catalog=[CatalogTool(t.name,True,t.description,t.input_schema,['planner'],enabled,'builtin',{},False) for t in tools]
        core=PlannerCycle(store=self.graph,transcript=self.tx,current=self.core.current,lifecycle_guard=self.core.guard,provider_resolver=self.core.provider,authorize=grant or self.core.authorize,asset_authorize=self.core.asset_authorize,additional_tools=tools,additional_catalog=catalog)
        return core,tools,catalog
    def test_explicit_graph_proof_persists_evidence_not_model_claim(self):
        # Extend fixture executor to perform actual goal UPDATE SQL in memory.
        oldexecute=self.db.execute
        def execute(q):
            if q.sql.startswith('UPDATE exploration_nodes SET state=$1'):
                self.db.queries.append(q)
                self.db.rows=[tuple([*r[:4],q.params[0],*r[5:]]) if r[0]==q.params[1] else r for r in self.db.rows]
                return 1
            return oldexecute(q)
        self.db.execute=execute
        core,_,_=self.extra()
        self.provider.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'p','name':'prove_goal','input':{'goal_id':2,'evidence_id':1}}]},'tool_use',{}),fixtures.text('claimed finished')]
        outcome=core.run(core.prepare(self.scope,'proof'))
        self.assertTrue(outcome.goals_met);self.assertEqual(self.db.rows[1][4],'met')
        self.assertIn((9,1,'proves',2),self.db.links)
        self.assertFalse(any(q.sql.startswith('UPDATE tasks') for q in self.db.queries))
    def test_denied_extra_never_changes_goal(self):
        def grant(context,action):return Grant(True,True,action.kind!='tool')
        core,_,_=self.extra(grant=grant)
        self.provider.responses=[({'role':'assistant','content':[{'type':'tool_use','id':'p','name':'prove_goal','input':{'goal_id':2,'evidence_id':1}}]},'tool_use',{})]
        outcome=core.run(core.prepare(self.scope,'denied'))
        self.assertFalse(outcome.goals_met);self.assertEqual(self.db.links,[])
    def test_unbound_or_disabled_extras_not_exposed(self):
        core,_,_=self.extra(enabled=False);self.provider.responses=[fixtures.text('nothing')]
        core.run(core.prepare(self.scope,'disabled'))
        names=[t['name'] for t in self.provider.calls[0]['tools']]
        self.assertEqual(names,['add_intent'])
    def test_reserved_name_collision_and_missing_catalog_rejected(self):
        tool=self.core.intent_tool(self.scope)
        base=dict(store=self.graph,transcript=self.tx,current=self.core.current,lifecycle_guard=self.core.guard,provider_resolver=self.core.provider,authorize=self.core.authorize,asset_authorize=self.core.asset_authorize)
        with self.assertRaises(ValueError):PlannerCycle(**base,additional_tools=[tool])
        _,tools,_=self.extra()
        with self.assertRaises(ValueError):PlannerCycle(**base,additional_tools=tools)
