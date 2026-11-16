"""Real owned host advance obtains cleanup; inert SQL and providers only."""
import unittest
from unittest.mock import patch
from dataclasses import replace
from artex.agent.engine import TaskEngineHandle
from artex.agent.activity_projection import ActivityProjector
from artex.agent.inflight_cleanup import InflightCleanupOwner,CleanupUnavailable
from artex.agent.bootstrap import BootstrapUnresolved
from artex.agent.runtime import Grant
import test_bootstrap_handle as boot
import test_activity_projection as projection

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=boot.BootstrapHandleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;self.sql=projection.DB();self.captured=[]
        one,all_=f.db.fetch_one,f.db.fetch_all
        def fetch_one(q):
            if q.sql.startswith(('SELECT exploration_id,deleted_at','INSERT INTO activity')):return self.sql.fetch_one(q)
            return one(q)
        def fetch_all(q):
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=f.db.receipt;return [(5,)] if r is not None and tuple(r[:7])==q.params else []
            if q.sql.startswith(('SELECT id,\n(metadata','SELECT state FROM task_archives')):return self.sql.fetch_all(q)
            return all_(q)
        f.db.fetch_one=fetch_one;f.db.fetch_all=fetch_all
        old=f.child_factory
        def child(row,token,scope,current,guard):
            base=old(row,token,scope,current,guard)
            f.f.planner.current=lambda s:current(scope,token)
            f.f.worker.current=lambda s,p:current(scope,token)
            self.projector=ActivityProjector(f.db,scope,transcript=base.planner.transcript,
                current=lambda s:current(scope,token),lifecycle_guard=guard,authorize=lambda p:Grant(True,True,True))
            return TaskEngineHandle(scope,token,store=base.store,planner=base.planner,planner_scope=base.planner_scope,
                worker=base.worker,worker_model=base.worker_model,worker_system=base.worker_system,owner=base.owner,
                current=current,lifecycle_guard=guard,single_process=True,activity_projector=self.projector,
                cleanup_authorize=lambda p:True,cleanup_current=lambda s:f.f.valid)
        f.child_factory=child;self.h=f.start();f.advance(self.h)
        original=InflightCleanupOwner.project
        def capture(owner,cap,projector,outcome,identity):
            self.captured.append((owner,cap,projector,outcome,identity))
            return original(owner,cap,projector,outcome,identity)
        self.patch=patch.object(InflightCleanupOwner,'project',capture);self.patch.start();self.addCleanup(self.patch.stop)
    def cancel(self):self.f.f.pp.hook=lambda:self.f.host.pause('5',timeout=0)
    def test_cancelled_receipt_projects_then_reuse_and_copy_denied(self):
        self.cancel();self.f.advance(self.h,1)
        self.assertTrue(self.h.token.cancelled);self.assertEqual(len(self.sql.rows),1)
        owner,cap,p,outcome,identity=self.captured[0]
        self.assertEqual(p.transcript.finalized_scoped_result(self.h._child.planner_scope.session_id,outcome.turn.id,identity).result.reason,'cancelled')
        self.assertFalse(self.f.manager._has_cleanup('5'))
        for value in (cap,replace(cap)):
            with self.assertRaises(CleanupUnavailable):owner.project(value,p,outcome,identity)
        self.assertEqual(len(self.sql.rows),1)
    def test_insert_failure_retains_exact_owner_without_model_retry(self):
        self.sql.fail=True;self.cancel()
        with self.assertRaises(BootstrapUnresolved):self.f.advance(self.h,1)
        self.assertTrue(self.h.unresolved);self.assertTrue(self.f.manager._has_cleanup('5'))
        self.assertEqual(len(self.captured),1);self.assertEqual(len(self.f.f.pp.calls),1)
        self.assertEqual(self.sql.rows,[])
        with self.assertRaises(BootstrapUnresolved):self.f.advance(self.h,2)
        self.assertEqual(len(self.f.f.pp.calls),1)
