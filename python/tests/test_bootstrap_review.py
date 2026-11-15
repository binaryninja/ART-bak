"""Independent bootstrap ownership/replay regression tests; inert fixtures only."""
import unittest
import test_bootstrap as fixture
from test_goal_planner import goals
from test_chat_runtime import text
from artex.agent.bootstrap import BootstrapUnavailable
from artex.manager import CancellationToken

class BootstrapReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.BootstrapTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_prepared_receipt_rejects_deleted_or_unlinked_goals(self):
        for damage in ('deleted','unlinked'):
            with self.subTest(damage=damage):
                if damage=='unlinked':
                    self.f.doCleanups();self.f=fixture.BootstrapTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
                self.f.run_bootstrap();calls=len(self.f.provider.calls)
                if damage=='deleted':self.f.db.rows=self.f.db.rows[:1]
                else:self.f.db.links=[]
                with self.assertRaises(BootstrapUnavailable):self.f.run_bootstrap()
                self.assertEqual(len(self.f.provider.calls),calls)
    def test_retained_planner_has_no_authority_after_uncertain_result(self):
        retained=[];original=self.f.coordinator.factory
        def factory(*args):
            planner=original(*args);retained.append((planner,args[0]));return planner
        self.f.coordinator.factory=factory
        self.f.provider.responses=[text('No goals were submitted')]
        def uncertain(*args):raise RuntimeError('journal storage unavailable')
        self.f.journal.unresolved=uncertain
        with self.assertRaises(BootstrapUnavailable):self.f.run_bootstrap()
        self.assertEqual(self.f.db.receipt[7],'running')
        calls=len(self.f.provider.calls);self.f.provider.responses=[goals(),text('submitted')]
        planner,scope=retained[0]
        with self.assertRaises(Exception):planner.run(scope,'unexpected-second-run','another goal',token=CancellationToken())
        self.assertEqual(len(self.f.provider.calls),calls)
    def test_finish_cas_contains_full_record_identity(self):
        self.f.run_bootstrap()
        # Inspect implementation-issued query by capturing at actual execution.
        self.f.doCleanups();self.f=fixture.BootstrapTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        queries=[];original=self.f.db.fetch_all
        def capture(q):queries.append(q);return original(q)
        self.f.db.fetch_all=capture;self.f.run_bootstrap()
        q=next(q for q in queries if q.sql.startswith('UPDATE public.artex_task_bootstrap'))
        where=q.sql.split('WHERE',1)[1].split('RETURNING',1)[0]
        self.assertIn('config_digest=',where)
        self.assertIn('request_id=',where)
        self.assertIn('exploration_id=',where)
    def test_prepared_replay_rechecks_authority_after_journal_read(self):
        self.f.run_bootstrap();original=self.f.db.fetch_all
        def invalidate(q):
            result=original(q)
            if q.sql.startswith('SELECT task_id,exploration_id,run_id'):self.f.valid=False
            return result
        self.f.db.fetch_all=invalidate
        with self.assertRaises(BootstrapUnavailable):self.f.run_bootstrap()
    def test_planner_authority_revoked_immediately_after_result_before_finish(self):
        retained=[];original_factory=self.f.coordinator.factory
        def factory(*args):
            planner=original_factory(*args);retained.append((planner,args[0]));return planner
        self.f.coordinator.factory=factory
        original_finish=self.f.journal.finish;attempts=[]
        def finish(*args,**kwargs):
            planner,scope=retained[0];before=len(self.f.provider.calls)
            self.f.provider.responses=[goals([{'text':'unexpected second goal'}]),text('second run')]
            try:
                planner.run(scope,'second-after-result','unexpected',token=CancellationToken())
            except Exception:pass
            attempts.append(len(self.f.provider.calls)-before)
            return original_finish(*args,**kwargs)
        self.f.journal.finish=finish
        self.f.run_bootstrap()
        self.assertEqual(attempts,[0])
