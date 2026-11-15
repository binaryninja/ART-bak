import unittest,tempfile,json
from contextlib import contextmanager
from dataclasses import replace
from artex.agent.goals import GoalPlanner,GoalScope,parse_goals
from artex.agent.chat import ChatBinding
from artex.agent.runtime import Grant
from artex.db.exploration import ExplorationStore
from artex.chat_transcripts import ChatTranscriptAdapter
from artex.transcripts import TranscriptStore
from artex.manager import CancellationToken
from test_planner_cycle import GraphDB
from test_chat_runtime import Provider,MODEL,text

def goals(items=None):
    return ({'role':'assistant','content':[{'type':'tool_use','id':'g1','name':'set_goals','input':{'goals':items or [{'text':'  explicit outcome  ','vulnclass':' IDOR '}]}}]},'tool_use',{})

class GoalTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=GraphDB();self.store=ExplorationStore(self.db,9);self.tx=ChatTranscriptAdapter(TranscriptStore(tmp.name))
        self.scope=GoalScope(5,9,1,'r1',MODEL,'Submit only explicitly requested final outcomes with set_goals. Constraints already handled.')
        self.valid=True;self.provider=Provider([goals(),text('submitted')]);self.approved=True
        @contextmanager
        def guard(scope):yield
        self.core=GoalPlanner(store=self.store,transcript=self.tx,current=lambda s:self.valid,lifecycle_guard=guard,provider_resolver=lambda s,m:ChatBinding(m,self.provider),authorize=lambda *a:Grant(self.approved,True,True),validate_input=lambda *a:True)
    def run_goal(self,client='one',**kwargs):return self.core.run(self.scope,client,'Explicit outcome',**kwargs)
    def test_actual_atomic_goal_and_root_lineage(self):
        out=self.run_goal();self.assertTrue(out.persisted)
        row=self.db.rows[-1];self.assertEqual(row[1],'goal');self.assertEqual(json.loads(row[2]),{'text':'explicit outcome','vulnclass':'IDOR'})
        self.assertEqual(row[4:6],('open','goals'));self.assertIn((9,1,'spawns',3),self.db.links)
        self.assertFalse(any(q.sql.startswith('UPDATE tasks') for q in self.db.queries))
    def test_durable_replay_no_second_insert(self):
        first=self.run_goal();count=len(self.db.rows)
        second=self.run_goal();self.assertTrue(second.reused);self.assertEqual(len(self.db.rows),count);self.assertEqual(second.result,first.result)
    def test_changed_scope_conflicts_before_effect(self):
        self.run_goal();count=len(self.db.rows);self.scope=replace(self.scope,revision='r2')
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(len(self.db.rows),count)
    def test_text_only_does_not_create_goal(self):
        self.provider=Provider([text('goal achieved')]);self.run_goal();self.assertEqual(len(self.db.rows),2)
    def test_denied_model_no_effect(self):
        self.approved=False;self.run_goal();self.assertEqual(len(self.db.rows),2)
    def test_link_failure_rolls_back_batch(self):
        self.db.execute=lambda q:0
        self.run_goal();self.assertEqual(len(self.db.rows),2)
    def test_final_identity_check_rolls_back(self):
        original=self.db.execute
        def execute(q):
            result=original(q);self.valid=False;return result
        self.db.execute=execute
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(len(self.db.rows),2)
    def test_cancelled_before_run_no_rows(self):
        token=CancellationToken();token.cancel()
        with self.assertRaises(Exception):self.run_goal(token=token)
        self.assertEqual(len(self.db.rows),2)
    def test_ambiguous_commit_blocks_retry_without_duplicate(self):
        original=self.tx.commit
        def fail(*a):raise RuntimeError('fixture persistence failure')
        self.tx.commit=fail
        with self.assertRaises(Exception):self.run_goal()
        count=len(self.db.rows);self.assertEqual(count,3)
        self.tx.commit=original
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(len(self.db.rows),count)
    def test_strict_structured_fields(self):
        for value in ({'text':' '},{'goals':[{'text':'x','met':True}]},{'text':'x','vulnclass':3},{'goals':[{'text':'x'}]*33}):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_goals(value)
        self.assertEqual(parse_goals({'goals':[],'text':' x '}),[{'text':'x'}])
    def test_scope_prerequisites_before_admission(self):
        self.core.validate_input=lambda *a:False
        with self.assertRaises(Exception):self.run_goal()
        self.assertEqual(self.db.queries,[])
