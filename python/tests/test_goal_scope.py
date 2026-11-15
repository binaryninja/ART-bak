"""Explicit normalized scope approval inside actual goal planning; inert SQL."""
import unittest
from contextlib import nullcontext
from dataclasses import replace
from artex.tools.task_scope import TaskScopeTools
from artex.db.task_scope import TaskScopeStore,normalize
from artex.agent.graph_context import ContextScope
from artex.agent.runtime import Grant
from artex.agent.chat import ChatUnavailable
import test_goal_constraints as fixtures
from test_goal_planner import goals
from test_chat_runtime import text

def scope_call():
    return ({'role':'assistant','content':[{'type':'tool_use','id':'s1','name':'add_task_scope','input':{'kind':'subdomain','value':'APP.EXAMPLE.TEST','reason':'explicit user scope'}}]},'tool_use',{})

class GoalScopeTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.IntegrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.rows=[];self.approved=True;self.proposals=[]
        one=self.f.db.fetch_one;allrows=self.f.db.fetch_all;execute=self.f.db.execute
        def fetch_one(q):
            if q.sql.startswith('SELECT id,deleted_at,exploration_id'):return (5,None,9)
            return one(q)
        def fetch_all(q):
            if q.sql.startswith('SELECT state FROM task_archives'):return []
            return allrows(q)
        def write(q):
            if q.sql.startswith('INSERT INTO task_scope'):self.rows.append(q.params);return 1
            return execute(q)
        self.f.db.fetch_one=fetch_one;self.f.db.fetch_all=fetch_all;self.f.db.execute=write
        def approve(entry):self.proposals.append(entry);return self.approved
        store=TaskScopeStore(self.f.db,5,lifecycle_guard=nullcontext,current=lambda:self.f.valid,approve=approve)
        self.adapter=TaskScopeTools(store,ContextScope(5,9,1,'r1'),gate=lambda *a:Grant(True,True,True),current=lambda s:self.f.valid,lifecycle_guard=self.f.guard)
        self.catalog=self.adapter.catalog(agents=['goals'])[0]
        self.f.provider.responses=[fixtures.constraint(),scope_call(),goals(),text('recorded')]
        self.f.core=self.build()
    def build(self,**kwargs):
        args=dict(task_scope=self.adapter,scope_catalog=self.catalog);args.update(kwargs);return self.f.build(**args)
    def test_actual_constraint_scope_goal_order_and_durable_replay(self):
        result=self.f.run_goal();self.assertTrue(result.persisted)
        self.assertEqual(self.rows[0][3],'app.example.test');self.assertEqual(self.rows[0][-1],'explicit user scope')
        self.assertEqual(len(self.f.db.constraints),1);self.assertEqual(len(self.f.db.rows),3)
        self.assertEqual(self.proposals,[normalize(5,'subdomain','app.example.test',reason='explicit user scope',source='agent')])
        self.f.core=self.build();self.assertTrue(self.f.run_goal().reused);self.assertEqual(len(self.rows),1)
    def test_model_scope_is_not_permission(self):
        self.approved=False;self.f.run_goal();self.assertEqual(self.rows,[]);self.assertEqual(len(self.proposals),1)
    def test_changed_scope_or_catalog_rejects_replay(self):
        self.f.run_goal();self.f.core=self.build(scope_catalog=replace(self.catalog,description='changed policy metadata'))
        with self.assertRaises(Exception):self.f.run_goal()
        self.assertEqual(len(self.rows),1)
    def test_foreign_run_and_guard_reject_before_model(self):
        self.adapter.scope=replace(self.adapter.scope,run_id=2)
        with self.assertRaises(ChatUnavailable):self.f.run_goal()
        self.assertEqual(self.f.provider.calls,[])
        self.adapter.scope=self.adapter._scope;self.adapter.guard=lambda s:nullcontext()
        with self.assertRaises(ValueError):self.build()
    def test_missing_disabled_or_unbound_catalog_rejected(self):
        for catalog in (None,replace(self.catalog,enabled=False),replace(self.catalog,agents=['worker']),replace(self.catalog,deferred=True)):
            with self.subTest(catalog=catalog),self.assertRaises(ValueError):self.build(scope_catalog=catalog)
    def test_input_policy_required_even_with_all_tools(self):
        self.f.core.validate_input=lambda *a:False
        with self.assertRaises(ChatUnavailable):self.f.run_goal()
        self.assertEqual(self.rows,[]);self.assertEqual(self.f.provider.calls,[])
    def test_removing_optional_scope_changes_durable_identity(self):
        self.f.run_goal();self.f.core=self.build(task_scope=None,scope_catalog=None)
        with self.assertRaises(Exception):self.f.run_goal()
        self.assertEqual(len(self.rows),1)
