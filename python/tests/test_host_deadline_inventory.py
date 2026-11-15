"""Synthetic ownership inventory; no positive timed runtime is enabled."""
import unittest
from unittest.mock import patch
from artex.agent.host import PollResult
from artex.manager import DeadlineOwnerSnapshot
from test_agent_host import host_for
from test_agent_runner import make_runner

class Tests(unittest.TestCase):
    def test_suspended_lease_is_not_capacity_but_prevents_false_shutdown(self):
        host=host_for(make_runner())
        with patch.object(host.manager,'deadline_owners',return_value=(DeadlineOwnerSnapshot('1','suspended'),)):
            state=host.poll()
            self.assertEqual((state.running,state.pending),(0,0))
            self.assertEqual((state.deadline_owners,state.deadline_pending),(1,0))
            self.assertFalse(state.all_settled)
            drain=host.drain(0)
            self.assertTrue(drain.workers_drained)
            self.assertFalse(drain.all_settled)

    def test_unsettled_registration_blocks_drain_not_fake_execution_slots(self):
        for phase in ('reserved','attached','quarantined'):
            with self.subTest(phase=phase):
                host=host_for(make_runner())
                with patch.object(host.manager,'deadline_owners',return_value=(DeadlineOwnerSnapshot('1',phase),)):
                    result=host.drain(0)
                    self.assertFalse(result.workers_drained)
                    self.assertEqual(result.settlement.deadline_pending,1)
                    self.assertEqual((result.settlement.running,result.settlement.pending),(0,0))

    def test_nonleased_poll_result_remains_compatible(self):
        self.assertTrue(PollResult(False).all_settled)
        host=host_for(make_runner())
        self.assertTrue(host.drain(0).all_settled)

class ActualOwnershipTests(unittest.TestCase):
    def setUp(self):
        import test_manager_deadline_ownership as fixture
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_real_suspended_lease_remains_after_execution_retirement(self):
        f=self.f;f.store.stamp_owned(30);lease=f.store.lease
        f.host.pause('5')
        state=f.host.poll()
        self.assertEqual(f.runner.handles(),())
        self.assertEqual((state.running,state.pending,state.deadline_owners,state.deadline_pending),(0,0,1,0))
        self.assertFalse(state.all_settled)
        self.assertTrue(f.host.drain(0).workers_drained)
        self.assertFalse(f.host.drain(0).all_settled)
        self.assertIs(f.store.validate_lease(lease),lease.clock)
        self.assertFalse(f.manager.owns_generation('5',f.h.token,f.h,allow_cancelled=True))

    def test_real_quarantined_reservation_keeps_execution_attachment(self):
        f=self.f;reservation=f.reserve()
        f.manager.mark_deadline_attempted(reservation)
        f.manager.quarantine_deadline_owner(reservation)
        f.h.cancel('synthetic cancellation')
        state=f.host.poll()
        self.assertEqual(f.runner.handles(),(f.h,))
        self.assertEqual((state.pending,state.deadline_owners,state.deadline_pending),(1,1,1))
        self.assertFalse(f.host.drain(0).workers_drained)
        self.assertFalse(f.manager.owns_generation('5',f.h.token,f.h))
