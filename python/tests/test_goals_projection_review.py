"""Independent goals-terminal projection tests with canonical temp transcripts."""
import unittest
from dataclasses import replace
import test_goals_activity_projection as fixture
from artex.agent.activity_projection import ProjectionUnavailable
from artex.agent.runtime import Grant

class GoalsProjectionReview(unittest.TestCase):
    def hook(self):
        f=fixture.GoalHookTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def receipt(self):
        f=fixture.GoalsProjectionTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def test_lifetime_revoked_after_transcript_commit_no_progress(self):
        f=self.hook();original=f.f.tx.commit
        def commit(*a,**kw):
            result=original(*a,**kw);f.f.valid=False;return result
        f.f.tx.commit=commit
        with self.assertRaises(Exception):f.f.run_goal()
        self.assertEqual(f.progress.rows,[])
        self.assertEqual(len(f.f.provider.calls),2)
    def test_canonical_goals_receipt_cannot_be_relabelled_planner(self):
        f=self.receipt();identity=dict(f.identity,role='planner')
        with self.assertRaises(Exception):f.f.p.project('planner',f.scope,f.outcome,identity)
        self.assertEqual(f.f.db.rows,[])
    def test_projection_permission_independent_of_model_permission(self):
        f=self.hook()
        # Construct a properly pinned projector with a separate denying grant.
        from artex.agent.activity_projection import ActivityProjector
        from artex.agent.goals import GoalPlanner
        core=f.f.core
        projector=ActivityProjector(f.f.db,f.f.scope,transcript=f.f.tx,current=core.current,
                    lifecycle_guard=core.guard,authorize=lambda p:Grant(False,True,True))
        f.f.core=GoalPlanner(store=f.f.store,transcript=f.f.tx,current=core.current,lifecycle_guard=core.guard,
                    provider_resolver=core.resolver,authorize=core.authorize,validate_input=core.validate_input,
                    activity_projector=projector)
        with self.assertRaises(ProjectionUnavailable):f.f.run_goal()
        self.assertEqual(f.progress.rows,[]);self.assertEqual(len(f.f.provider.calls),2)
        self.assertFalse(any(q.sql.startswith('UPDATE tasks') for q in f.f.db.queries))
    def test_denied_model_terminal_is_error_not_task_success(self):
        f=self.hook();f.f.approved=False
        outcome=f.f.run_goal()
        self.assertEqual(f.f.provider.calls,[])
        self.assertNotEqual(outcome.result.reason,'completed')
        self.assertEqual(len(f.progress.rows),1)
        self.assertTrue(f.progress.rows[0][2][6])
        self.assertFalse(any(q.sql.startswith('UPDATE tasks') for q in f.f.db.queries))
