"""Actual host→bootstrap→engine→planner/worker cleanup, inert providers/SQL."""
import unittest
from dataclasses import replace
from artex.agent.engine import TaskEngineHandle
from artex.agent.bootstrap import BootstrapUnresolved
from artex.agent.inflight_cleanup import CleanupUnavailable
import test_bootstrap_handle as fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.BootstrapHandleTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.allowed=True;self.proposals=[];self.h=None
        old=self.f.child_factory
        def child(row,token,scope,current,guard):
            base=old(row,token,scope,current,guard)
            self.f.f.planner.current=lambda s:current(scope,token)
            self.f.f.worker.current=lambda s,p:current(scope,token)
            return TaskEngineHandle(scope,token,store=base.store,planner=base.planner,planner_scope=base.planner_scope,
                worker=base.worker,worker_model=base.worker_model,worker_system=base.worker_system,owner=base.owner,
                current=current,lifecycle_guard=guard,single_process=True,
                cleanup_authorize=lambda p:self.proposals.append(p) or self.allowed,cleanup_current=lambda s:self.f.f.valid)
        self.f.child_factory=child
        # Runner closes over fixture.child_factory at call time.
        self.h=self.f.start();self.f.advance(self.h)
        oldfetch=self.f.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT task_id FROM public.artex_task_bootstrap'):
                r=self.f.db.receipt;return [(5,)] if r is not None and tuple(r[:7])==q.params else []
            return oldfetch(q)
        self.f.db.fetch_all=fetch
    def test_actual_planner_commits_then_releases_cap(self):
        value=self.f.advance(self.h,1)
        self.assertEqual(value.action,'planner');self.assertFalse(self.f.manager._has_cleanup('5'))
        self.assertEqual(len(self.f.f.pp.calls),2)
    def test_pause_racing_completed_model_records_cancelled_no_new_work(self):
        self.f.f.pp.hook=lambda:self.f.host.pause('5',timeout=0)
        value=self.f.advance(self.h,1)
        self.assertEqual(value.action,'planner');self.assertFalse(self.h.unresolved)
        self.assertFalse(self.f.manager._has_cleanup('5'))
        self.assertEqual(len(self.f.f.pp.calls),1)
        self.assertFalse(self.proposals[-1].operation=='model')
    def test_denied_cleanup_retains_unresolved_owner(self):
        self.f.f.pp.hook=lambda:setattr(self,'allowed',False)
        with self.assertRaises(BootstrapUnresolved):self.f.advance(self.h,1)
        self.assertTrue(self.h.unresolved);self.assertTrue(self.f.manager._has_cleanup('5'))
        self.assertEqual(len(self.f.f.pp.calls),2)
    def test_idle_direct_planner_cannot_reserve_cleanup(self):
        planner=self.h._child.planner;scope=self.h._child.planner_scope
        # Ordinary execution current also rejects idle outer invocation.
        with self.assertRaises(Exception):planner.prepare(scope,'outside')
        self.assertEqual(self.f.f.pp.calls,[])
    def test_actual_active_provider_freeze_drains_only_after_cleanup(self):
        # Synthetic positive clock pin solely exercises existing freeze protocol;
        # public constructors/admission still reject timed tasks.
        from datetime import datetime,timezone,timedelta
        from artex.db.task_deadline import TaskDeadlineStore
        h=self.h;h._configuration=(*h._configuration[:-1],10);h._task['timeout_seconds']=10
        self.f.manager._tasks['5']['timeout_seconds']=10
        start=datetime(2026,1,1,tzinfo=timezone.utc);end=start+timedelta(seconds=10)
        old=self.f.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT timeout_seconds,first_run_at'):return [(10,start,end)]
            if q.sql.startswith('SELECT timeout_seconds'):return [(10,)]
            if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):return [(1,)]
            if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(start,end)]
            return old(q)
        self.f.db.fetch_all=fetch
        store=TaskDeadlineStore(self.f.db,manager=self.f.manager,handle=h,receipt=h._receipt,
            lifecycle_guard=h.coordinator.guard,authorize=lambda *a:True)
        store.stamp_owned(10);observed=[]
        self.f.f.pp.hook=lambda:observed.append(self.f.host.freeze_deadline('5',h.token,h,store.lease,timeout=0))
        result=self.f.advance(h,1)
        self.assertEqual(result.action,'planner');self.assertEqual(len(self.f.f.pp.calls),1)
        self.assertFalse(observed[0][1]);self.assertFalse(h.unresolved)
        self.assertFalse(self.f.manager._has_cleanup('5'))
        self.assertTrue(self.f.host.freeze_deadline('5',h.token,h,store.lease,timeout=0)[1])
        self.assertEqual(self.f.manager.deadline_owners()[0].state,'drained')
    def test_copied_cap_and_foreign_turn_denied_during_actual_run(self):
        from artex.agent.runtime import RunResult
        owner=self.h._child.cleanup;checked=[]
        def probe():
            cap,rec=next(iter(owner._records.items()))
            fake=replace(cap)
            with self.assertRaises(CleanupUnavailable):owner.finish(fake)
            wrong=replace(rec['turn'],id='f'*32)
            with self.assertRaises(CleanupUnavailable):owner.commit(cap,wrong,RunResult('cancelled',rec['turn'].messages,{},0,0))
            checked.append(cap)
        self.f.f.pp.hook=probe
        self.f.advance(self.h,1)
        self.assertTrue(checked)
        with self.assertRaises(CleanupUnavailable):owner.finish(checked[0])
        self.assertFalse(self.f.manager._has_cleanup('5'))
