"""Independent final handoff pins and freshness; inert original-owner fixture."""
import unittest
from dataclasses import replace
from artex.agent.owned_finalization import OwnedFinalization,FinalizationUnavailable
from artex.agent.expiry_observer import OwnedExpiryObserver
import test_owned_finalization as fixture

class FinalizationReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_last_observation_callback_revokes_start_before_allocation(self):
        f=self.f;starts=[];allowed=[True]
        def start(receipt):starts.append(receipt);return allowed[0]
        def read(receipt):
            if len(starts)>=2:allowed[0]=False
            return True
        observer=OwnedExpiryObserver(f.f.host,f.f.kernel,authorize_observation=read,authorize_freeze=lambda r:True)
        controller=OwnedFinalization(observer,replace(f.plan,start_authorize=start))
        with self.assertRaises(FinalizationUnavailable):controller.run_once()
        self.assertEqual(f.calls,[]);self.assertIsNone(f.row)
    def test_stale_ready_snapshot_rechecks_database_before_handoff(self):
        f=self.f;self.assertEqual(f.f.observer.tick().phase,'ready')
        f.f.expired=False
        result=f.controller.run_once()
        self.assertEqual(result.phase,'waiting_database');self.assertEqual(f.calls,[])
        self.assertIsNone(f.row)
    def test_transcript_pin_change_from_start_grant_prevents_allocation(self):
        f=self.f
        def start(receipt):
            f.transcript.recovery_guard=lambda *a:True
            return True
        controller=OwnedFinalization(f.f.observer,replace(f.plan,start_authorize=start))
        with self.assertRaises(FinalizationUnavailable):controller.run_once()
        self.assertIsNone(f.row);self.assertEqual(f.calls,[])
    def test_competing_coordinator_after_transfer_cannot_run_model(self):
        f=self.f;seen=[]
        other=OwnedFinalization(f.f.observer,f.plan)
        def hook():
            with self.assertRaises(FinalizationUnavailable):other.run_once()
            seen.append(f.f.observer.tick().phase)
        f.hook=hook
        self.assertEqual(f.controller.run_once().phase,'settled')
        self.assertEqual(seen,['transferred']);self.assertEqual(len(f.calls),1)
    def test_missing_journal_schema_after_handoff_retains_owner_no_model_retry(self):
        f=self.f;original=f.db.fetch_one;attempts=[]
        def fetch(query):
            if query.sql.startswith('INSERT INTO public.artex_final_assessment'):
                attempts.append(query)
                raise RuntimeError('synthetic missing schema')
            return original(query)
        f.db.fetch_one=fetch
        with self.assertRaises(FinalizationUnavailable):f.controller.run_once()
        self.assertEqual(f.calls,[]);self.assertEqual(len(attempts),1)
        self.assertEqual(f.manager.deadline_owners()[0].state,'quarantined')
        with self.assertRaises(FinalizationUnavailable):f.controller.run_once()
        self.assertEqual(len(attempts),1);self.assertEqual(f.calls,[])
        self.assertEqual(f.f.observer.tick().phase,'transferred')
