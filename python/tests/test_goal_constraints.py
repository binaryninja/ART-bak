import unittest,tempfile
from contextlib import contextmanager
from dataclasses import replace
from copy import deepcopy
from artex.agent.goals import *
from artex.tools.constraints import ConstraintScope,ConstraintTools,ConstraintStore
from artex.db.tool_catalog import CatalogTool
from artex.agent.runtime import Grant
from artex.agent.chat import ChatBinding
from artex.agent.planner_prompts import GOALS_DEFAULT
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from test_planner_cycle import GraphDB
from test_goal_planner import goals
from test_chat_runtime import Provider,MODEL,text

def constraint():
    return ({'role':'assistant','content':[{'type':'tool_use','id':'c1','name':'set_constraints','input':{'text':'No writes to example.test'}}]},'tool_use',{})
class DB(GraphDB):
    def __init__(self):super().__init__();self.constraints=[]
    def fetch_one(self,q):
        if q.sql.startswith('INSERT INTO task_constraints'):
            self.queries.append(q);self.constraints.append(q.params);return (len(self.constraints),)
        return super().fetch_one(q)
    @contextmanager
    def transaction(self):
        old=deepcopy(self.constraints)
        try:
            with super().transaction() as tx:yield tx
        except BaseException:self.constraints=old;raise

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=DB();self.graph=ExplorationStore(self.db,9);self.tx=ChatTranscriptAdapter(TranscriptStore(tmp.name));self.valid=True;self.grant=True
        @contextmanager
        def guard(scope):yield
        self.guard=guard
        self.adapter=ConstraintTools(ConstraintStore(self.db,9),self.graph,ConstraintScope(5,9,1,'r1','goals'),current=lambda s:self.valid,lifecycle_guard=guard,authorize=lambda *a:Grant(True,True,True),proposal_authorize=lambda *a:Grant(self.grant,True,True))
        tool=self.adapter.tools()[0]
        self.catalog=CatalogTool(tool.name,True,tool.description,tool.input_schema,['goals'],True,'builtin',{},False)
        self.scope=GoalScope(5,9,1,'r1',MODEL,GOALS_DEFAULT)
        self.provider=Provider([constraint(),goals(),text('recorded')]);self.core=self.build()
    def build(self,**kw):
        args=dict(store=self.graph,transcript=self.tx,current=lambda s:self.valid,lifecycle_guard=self.guard,provider_resolver=lambda s,m:ChatBinding(m,self.provider),authorize=lambda *a:Grant(True,True,True),validate_input=lambda *a:True,constraints=self.adapter,constraint_catalog=self.catalog)
        args.update(kw);return GoalPlanner(**args)
    def run_goal(self):return self.core.run(self.scope,'stable','Explicit outcome','No writes to example.test')
    def test_actual_constraints_then_goals_and_replay(self):
        self.assertTrue(self.run_goal().persisted)
        self.assertEqual(self.db.constraints,[(9,'deny','No writes to example.test','goals')])
        self.assertEqual(len(self.db.rows),3)
        self.core=self.build();self.assertTrue(self.run_goal().reused)
        self.assertEqual(len(self.db.constraints),1);self.assertEqual(len(self.db.rows),3)
    def test_model_proposal_not_operator_permission(self):
        self.grant=False;self.run_goal();self.assertEqual(self.db.constraints,[])
    def test_foreign_scope_reject_before_admission(self):
        for patch in ({'task_id':6},{'run_id':2},{'revision':'other'},{'origin':'human'}):
            self.adapter.scope=replace(self.core._constraint_scope,**patch)
            with self.subTest(patch=patch),self.assertRaises(ChatUnavailable):self.run_goal()
        self.assertEqual(self.db.queries,[])
    def test_foreign_executor_and_disabled_catalog_reject(self):
        self.adapter.store.executor=DB()
        with self.assertRaises(ValueError):self.build()
        self.adapter.store.executor=self.db
        with self.assertRaises(ValueError):self.build(constraint_catalog=replace(self.catalog,enabled=False))
        with self.assertRaises(ValueError):self.build(constraint_catalog=None)
    def test_catalog_change_breaks_durable_replay(self):
        self.run_goal();self.core=self.build(constraint_catalog=replace(self.catalog,description='Reviewed changed description'))
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(len(self.db.constraints),1)
    def test_default_goals_only_and_removal_conflicts_existing_receipt(self):
        self.run_goal();self.core=self.build(constraints=None,constraint_catalog=None)
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(len(self.db.constraints),1)
    def test_asset_scope_prerequisite_still_required(self):
        self.core.validate_input=lambda *a:False
        with self.assertRaises(ChatUnavailable):self.run_goal()
        self.assertEqual(self.db.queries,[])
