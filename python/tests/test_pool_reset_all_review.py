"""Independent reset-all callback boundary regressions; synthetic health only."""
import unittest
import test_pool_health as fixtures
from artex.health import Registry,State

class ResetAllReview(unittest.TestCase):
    def test_revoked_scope_after_first_forget_stops_later_persistence(self):
        f=fixtures.PoolTests();calls=[];live=[True]
        def forget(id):calls.append(id);live[0]=False
        service=f.make([[],[],[]],Registry(forget=forget));service.current=lambda *a:live[0]
        f.registry.restore(7,State(1));f.registry.restore(8,State(1))
        response=f.call('reset',{})
        self.assertEqual(response.status,503)
        self.assertTrue(response.json()['needs_reconciliation'])
        self.assertEqual(calls,[7])
    def test_forget_callback_replacement_does_not_retarget_remaining_effects(self):
        f=fixtures.PoolTests();calls=[];foreign=[]
        def forget(id):calls.append(id);f.registry._forget=foreign.append
        service=f.make([[],[],[]],Registry(forget=forget))
        f.registry.restore(7,State(1));f.registry.restore(8,State(1))
        response=f.call('reset',{})
        self.assertEqual(response.status,503)
        self.assertEqual(foreign,[])
