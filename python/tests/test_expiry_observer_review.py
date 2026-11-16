"""Independent original-owner expiry phase fencing; inert SQL fixture."""
import unittest
from artex.agent.expiry_observer import OwnedExpiryObserver
import test_expiry_observer as fixture

class ExpiryReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_last_read_grant_quarantines_owner_no_ready_receipt(self):
        f=self.f;f.stamp();f.expired=True
        def read(receipt):
            queries=sum('deadline_at<=clock_timestamp()' in q.sql for q in f.f.f.sql)
            if queries>=2:
                slot=f.host.manager._deadline_slots['5'];slot.state='quarantined'
            return True
        observer=OwnedExpiryObserver(f.host,f.kernel,authorize_observation=read,authorize_freeze=lambda r:True)
        with self.assertRaises(Exception):observer.tick()
    def test_final_read_callback_freeze_policy_revocation_not_ready(self):
        f=self.f;f.stamp();f.expired=True;allowed=[True]
        def read(receipt):
            if sum('deadline_at<=clock_timestamp()' in q.sql for q in f.f.f.sql)>=2:allowed[0]=False
            return True
        observer=OwnedExpiryObserver(f.host,f.kernel,authorize_observation=read,authorize_freeze=lambda r:allowed[0])
        with self.assertRaises(Exception):observer.tick()
