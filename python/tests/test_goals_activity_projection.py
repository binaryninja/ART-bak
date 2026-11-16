"""Goals terminal receipt usage, not proof of initialization/task completion."""
import unittest
from dataclasses import replace
from artex.agent.goals import GoalScope
from artex.agent.activity_projection import ActivityProjector,ProjectionUnavailable,ProjectionUncertain
from artex.agent.chat import ChatOutcome
from artex.agent.runtime import RunResult
import test_activity_projection as fixture

class GoalsProjectionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.scope=GoalScope(5,9,71,'r1',self.f.s.model,'explicit goals only')
        self.identity={'task_id':5,'exploration_id':9,'run_id':71,'revision':'r1','role':'goals','config_digest':'test-goals-config'}
        self.turn=self.f.t.begin_scoped(self.scope.session_id,'initialize',self.f.user,self.identity)
        self.result=RunResult('completed',[self.f.user,{'role':'assistant','content':[{'type':'text','text':'submitted goals'}]}],{'input_tokens':8,'output_tokens':3,'cache_read_tokens':2,'cache_write_tokens':0},2,1)
        self.f.t.commit(self.scope.session_id,self.turn,self.result)
        self.outcome=ChatOutcome(self.turn,self.result,True)
    def project(self):return self.f.p.project('goals',self.scope,self.outcome,self.identity)
    def test_goals_identity_and_planner_display_usage_once(self):
        self.assertEqual(self.project(),(1,));self.assertEqual(self.project(),(1,))
        self.assertEqual(len(self.f.db.rows),1)
        row=self.f.db.rows[0];params=row[2]
        self.assertEqual(row[1]['role'],'goals');self.assertEqual(params[2],'planner')
        self.assertIsNone(params[1]);self.assertEqual(params[10:14],(8,3,2,0))
        self.assertEqual(params[8],'submitted goals')
    def test_caller_text_and_usage_never_authoritative(self):
        self.outcome=replace(self.outcome,result=RunResult('completed',[],{'input_tokens':9999},1,0))
        self.project();self.assertEqual(self.f.db.rows[0][2][10],8)
    def test_pending_receipt_no_projection(self):
        turn=self.f.t.begin_scoped(self.scope.session_id,'pending',self.f.user,self.identity)
        self.outcome=ChatOutcome(turn,self.result,True)
        with self.assertRaises(ProjectionUnavailable):self.project()
        self.assertEqual(self.f.db.rows,[])
    def test_foreign_goals_generation_no_projection(self):
        self.identity['run_id']=72
        with self.assertRaises(ProjectionUnavailable):self.project()
        self.assertEqual(self.f.db.rows,[])
    def test_insert_failure_uncertainty_preserves_transcript(self):
        self.f.db.fail=True
        with self.assertRaises(ProjectionUncertain):self.project()
        self.assertIsNotNone(self.f.t.finalized_scoped_result(self.scope.session_id,self.turn.id,self.identity))
        self.assertEqual(self.f.db.rows,[])

class GoalHookTests(unittest.TestCase):
    def setUp(self):
        import test_goal_planner as goals_fixture
        from artex.agent.goals import GoalPlanner
        self.f=goals_fixture.GoalTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.progress=fixture.DB();db=self.f.db;one,all_=db.fetch_one,db.fetch_all
        def fetch_one(q):
            if q.sql.startswith(('SELECT exploration_id,deleted_at','INSERT INTO activity')):return self.progress.fetch_one(q)
            return one(q)
        def fetch_all(q):
            if q.sql.startswith(('SELECT id,\n','SELECT state FROM task_archives')):return self.progress.fetch_all(q)
            return all_(q)
        db.fetch_one,db.fetch_all=fetch_one,fetch_all
        original=self.f.core
        self.projector=ActivityProjector(db,self.f.scope,transcript=self.f.tx,current=original.current,lifecycle_guard=original.guard,authorize=lambda p:fixture.Grant(True,True,True))
        self.f.core=GoalPlanner(store=self.f.store,transcript=self.f.tx,current=original.current,lifecycle_guard=original.guard,provider_resolver=original.resolver,authorize=original.authorize,validate_input=original.validate_input,activity_projector=self.projector)
    def test_actual_goals_turn_projects_terminal_usage_without_replay(self):
        self.f.run_goal();calls=len(self.f.provider.calls)
        self.assertEqual(len(self.progress.rows),1);self.assertEqual(self.progress.rows[0][1]['role'],'goals')
        self.assertEqual(self.progress.rows[0][2][10:12],(2,1))
        self.f.run_goal();self.assertEqual(len(self.f.provider.calls),calls);self.assertEqual(len(self.progress.rows),1)
    def test_projection_failure_propagates_after_canonical_commit(self):
        self.progress.fail=True
        with self.assertRaises(ProjectionUncertain):self.f.run_goal()
        self.assertEqual(len(self.f.provider.calls),2);self.assertEqual(self.progress.rows,[])
        # Reconciliation may use the same finalized receipt, never another model call.
        self.progress.fail=False;self.f.run_goal()
        self.assertEqual(len(self.f.provider.calls),2);self.assertEqual(len(self.progress.rows),1)
    def test_cancellation_and_projector_rebind_prevent_effects(self):
        from artex.manager import CancellationToken
        token=CancellationToken();token.cancel()
        with self.assertRaises(Exception):self.f.run_goal(token=token)
        self.assertEqual(self.progress.rows,[]);self.assertEqual(self.f.provider.calls,[])
        self.f.core.activity_projector=None
        with self.assertRaises(Exception):self.f.run_goal()
        self.assertEqual(self.f.provider.calls,[])
