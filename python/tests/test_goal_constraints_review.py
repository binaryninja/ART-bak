"""Independent optional goals/constraints composition tests, inert providers/SQL."""
import unittest
from copy import deepcopy
from dataclasses import replace
from artex.agent.chat import ChatUnavailable
from artex.agent.runtime import Grant
import test_goal_constraints as fixtures
from test_chat_runtime import text

class GoalConstraintReview(unittest.TestCase):
    setUp=fixtures.IntegrationTests.setUp
    build=fixtures.IntegrationTests.build
    run_goal=fixtures.IntegrationTests.run_goal
    def test_catalog_default_enters_proposal_authorization_and_receipt_identity(self):
        catalog=deepcopy(self.catalog)
        catalog.schema['properties']['type']['default']='allow'
        proposals=[]
        self.adapter.proposals=lambda scope,items:proposals.append(items) or Grant(True,True,False)
        self.core=self.build(constraint_catalog=catalog)
        self.run_goal()
        self.assertEqual(proposals[0][0].kind,'allow')
        self.assertEqual(self.db.constraints,[])
        # Even denied durable turns cannot replay under changed defaults.
        catalog.schema['properties']['type']['default']='deny'
        self.core=self.build(constraint_catalog=catalog)
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(len(proposals),1)
    def test_invalid_native_schema_fails_before_provider_and_transcript(self):
        catalog=deepcopy(self.catalog)
        catalog.schema['properties']['foreign_scope']={'type':'integer'}
        self.core=self.build(constraint_catalog=catalog)
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(self.provider.calls,[]);self.assertEqual(self.db.queries,[])
    def test_asset_scope_validation_cannot_be_satisfied_by_constraint_adapter(self):
        self.core.validate_input=lambda *a:1
        with self.assertRaises(ChatUnavailable):self.run_goal()
        self.assertEqual(self.provider.calls,[]);self.assertEqual(self.db.queries,[])
    def test_goals_only_fresh_run_exposes_no_constraint_tool(self):
        self.provider.responses=[text('No structured writes requested')]
        self.core=self.build(constraints=None,constraint_catalog=None)
        self.scope=replace(self.scope,system='Only decompose outcomes with set_goals. Constraints already handled by operator.')
        self.assertTrue(self.run_goal().persisted)
        names=[t['name'] for t in self.provider.calls[0]['tools']]
        self.assertEqual(names,['set_goals']);self.assertEqual(self.db.constraints,[])
    def test_shared_guard_replacement_rejects_before_durable_admission(self):
        from contextlib import nullcontext
        self.adapter.guard=lambda scope:nullcontext()
        with self.assertRaises(ChatUnavailable):self.run_goal()
        self.assertEqual(self.provider.calls,[]);self.assertEqual(self.db.queries,[])
