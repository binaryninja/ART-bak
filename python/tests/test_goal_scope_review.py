"""Independent optional goal-scope integration review; inert providers/SQL only."""
import unittest
from copy import deepcopy
from dataclasses import replace
import test_goal_scope as fixture
from test_goal_planner import goals
from test_chat_runtime import text
from artex.agent.chat import ChatUnavailable
from artex.db.exploration import ExplorationStore

class GoalScopeReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.GoalScopeTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_catalog_default_cannot_bypass_normalized_scope_approval(self):
        schema=deepcopy(self.f.catalog.schema);schema['properties']['kind']['default']='root_domain'
        self.f.f.core=self.f.build(scope_catalog=replace(self.f.catalog,schema=schema))
        call=fixture.scope_call();call[0]['content'][0]['input']={'value':'example.test','reason':'model inferred broad scope'}
        self.f.f.provider.responses=[call,goals(),text('done')];self.f.approved=False
        self.f.f.run_goal()
        self.assertEqual(self.f.rows,[]);self.assertEqual(len(self.f.proposals),1)
        self.assertEqual(self.f.proposals[0].kind,'root_domain')
        self.assertEqual(self.f.proposals[0].domain,'example.test')
    def test_metadata_default_change_invalidates_existing_receipt(self):
        self.f.f.run_goal();count=len(self.f.f.provider.calls)
        schema=deepcopy(self.f.catalog.schema);schema['properties']['reason']['default']='different source annotation'
        self.f.f.core=self.f.build(scope_catalog=replace(self.f.catalog,schema=schema))
        with self.assertRaises(Exception):self.f.f.run_goal()
        self.assertEqual(len(self.f.f.provider.calls),count);self.assertEqual(len(self.f.rows),1)
    def test_provider_resolution_rebinding_adapter_stops_before_model(self):
        original=self.f.f.core.resolver
        def resolve(*a):
            result=original(*a);self.f.f.core.task_scope=None;return result
        self.f.f.core.resolver=resolve
        with self.assertRaises(Exception):self.f.f.run_goal()
        self.assertEqual(self.f.f.provider.calls,[]);self.assertEqual(self.f.rows,[])
    def test_store_identity_cannot_be_swapped_even_same_executor(self):
        self.f.f.core.store=ExplorationStore(self.f.f.db,9)
        with self.assertRaises(ChatUnavailable):self.f.f.run_goal()
        self.assertEqual(self.f.f.provider.calls,[])
    def test_input_callback_cannot_replace_or_downgrade_graph(self):
        def validate(*a):self.f.f.core.store.read_only=True;return True
        self.f.f.core.validate_input=validate
        with self.assertRaises(ChatUnavailable):self.f.f.run_goal()
        self.assertEqual(self.f.f.provider.calls,[]);self.assertEqual(self.f.rows,[])
    def test_native_schema_cannot_be_changed_to_accept_unrestricted_fields(self):
        schema=deepcopy(self.f.catalog.schema);schema['additionalProperties']=True
        self.f.f.core=self.f.build(scope_catalog=replace(self.f.catalog,schema=schema))
        with self.assertRaises(Exception):self.f.f.run_goal()
        self.assertEqual(self.f.f.provider.calls,[]);self.assertEqual(self.f.rows,[])
