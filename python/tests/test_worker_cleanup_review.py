"""Independent actual host→outer→engine→worker cleanup ownership tests."""
import unittest
import test_inflight_cleanup as fixture
from artex.agent.bootstrap import BootstrapUnresolved

class WorkerCleanupReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.f.f.seed()
    def test_canonical_removed_by_settlement_grant_no_worker_done(self):
        f=self.f;owner=f.h._child.cleanup;removed=[]
        class Proposals(list):
            def append(self,proposal):
                super().append(proposal)
                if proposal.operation=='worker_settle' and not removed:
                    with owner.transcript.store._locked(proposal.session_id,owner.transcript.agent_id) as (directory,name):
                        owner.transcript.store._write(directory,name,[])
                    removed.append(True)
        f.proposals=Proposals()
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertTrue(removed)
        self.assertEqual(f.h._child.worker.store.get(3).node.state,'running')
        self.assertTrue(f.f.manager._has_cleanup('5'));self.assertTrue(f.h.unresolved)
    def test_pause_between_created_turn_and_cleanup_reservation_retains_owner(self):
        f=self.f;transcript=f.h._child.worker.transcript;original=transcript.begin_scoped;created=[]
        def begin(*args,**kw):
            turn=original(*args,**kw);created.append(turn)
            f.f.host.pause('5',timeout=0)
            return turn
        transcript.begin_scoped=begin
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertEqual(len(created),1);self.assertEqual(f.f.f.wp.calls,[])
        self.assertTrue(f.h.unresolved)
        self.assertEqual(f.h._child.worker.store.get(3).node.state,'running')
        self.assertFalse(f.f.host.drain(0).all_settled)
    def test_worker_cleanup_finishes_only_original_claim_after_pause(self):
        f=self.f;f.f.f.wp.hook=lambda:f.f.host.pause('5',timeout=0)
        result=f.f.advance(f.h,1)
        self.assertEqual(result.action,'worker');self.assertEqual(len(f.f.f.wp.calls),1)
        self.assertEqual(f.h._child.worker.store.get(3).node.state,'open')
        self.assertFalse(f.f.manager._has_cleanup('5'))
    def test_last_commit_grant_revoking_configuration_prevents_record_write(self):
        f=self.f;owner=f.h._child.cleanup;seen=[]
        class Proposals(list):
            def append(self,proposal):
                super().append(proposal)
                if proposal.operation=='commit':
                    seen.append(proposal)
                    if len(seen)==2:f.f.f.valid=False
        f.proposals=Proposals()
        with self.assertRaises(BootstrapUnresolved):f.f.advance(f.h,1)
        self.assertGreaterEqual(len(seen),2)
        records=list(owner._records.values());self.assertEqual(len(records),1)
        rec=records[0]
        evidence=owner.transcript.finalized_scoped_result(rec['session'],rec['turn'].id,rec['identity'])
        self.assertIsNone(evidence)
    def test_freeze_between_created_turn_and_reservation_never_drains_clean(self):
        from datetime import datetime,timezone,timedelta
        from artex.db.task_deadline import TaskDeadlineStore
        f=self.f;h=f.h
        # Protocol-only positive lease fixture; public timeout admission stays off.
        h._configuration=(*h._configuration[:-1],10);h._task['timeout_seconds']=10
        f.f.manager._tasks['5']['timeout_seconds']=10
        start=datetime(2026,1,1,tzinfo=timezone.utc);end=start+timedelta(seconds=10)
        oldfetch=f.f.db.fetch_all
        def fetch(q):
            if q.sql.startswith('SELECT timeout_seconds,first_run_at'):return [(10,start,end)]
            if q.sql.startswith('SELECT timeout_seconds'):return [(10,)]
            if q.sql.startswith('SELECT 1 FROM public.artex_task_bootstrap'):return [(1,)]
            if q.sql.startswith('UPDATE tasks SET first_run_at'):return [(start,end)]
            return oldfetch(q)
        f.f.db.fetch_all=fetch
        clock=TaskDeadlineStore(f.f.db,manager=f.f.manager,handle=h,receipt=h._receipt,
            lifecycle_guard=h.coordinator.guard,authorize=lambda *a:True)
        clock.stamp_owned(10)
        transcript=h._child.worker.transcript;original=transcript.begin_scoped;observed=[]
        def begin(*args,**kw):
            turn=original(*args,**kw)
            observed.append(f.f.host.freeze_deadline('5',h.token,h,clock.lease,timeout=0))
            return turn
        transcript.begin_scoped=begin
        with self.assertRaises(BootstrapUnresolved):f.f.advance(h,1)
        self.assertEqual(len(observed),1);self.assertFalse(observed[0][1])
        self.assertEqual(f.f.f.wp.calls,[]);self.assertTrue(h.unresolved)
        self.assertEqual(h._child.worker.store.get(3).node.state,'running')
        from artex.manager import TaskConflict
        with self.assertRaises(TaskConflict):
            f.f.host.freeze_deadline('5',h.token,h,clock.lease,timeout=0)
        self.assertEqual(f.f.manager.deadline_owners()[0].state,'quarantined')
