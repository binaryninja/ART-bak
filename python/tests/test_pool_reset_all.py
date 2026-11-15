import unittest
from artex.health import Registry,State
from test_pool_health import PoolTests,PROFILE

class ResetAllTests(PoolTests):
    def test_complete_orphan_ids_approved_without_secret_snapshot(self):
        forgotten=[];self.make([[],[],[PROFILE]],Registry(forget=forgotten.append))
        self.registry.restore(19,State(1,last_error='secret'));self.registry.restore(7,State(2))
        self.registry.snapshot=lambda:(_ for _ in ()).throw(AssertionError('unbounded snapshot'))
        approved=[];self.service.mutate=lambda req,scope,p:approved.append(p) or True
        r=self.call('reset',{'id':0});self.assertEqual(r.status,200)
        self.assertEqual(approved[0].ids,(7,19));self.assertEqual(approved[0].operation,'reset_all')
        self.assertEqual(forgotten,[7,19]);self.assertEqual(self.registry.inventory(10)[1],())
    def test_inventory_changed_during_approval_no_effect(self):
        forgotten=[];self.make([],Registry(forget=forgotten.append));self.registry.restore(7,State(1))
        def approve(*a):self.registry.restore(8,State(1));return True
        self.service.mutate=approve
        self.assertEqual(self.call('reset',{}).status,503);self.assertEqual(forgotten,[])
        self.assertEqual(self.registry.inventory(10)[1],(7,8))
    def test_same_ids_changed_state_denies_before_effect(self):
        self.make([]);self.registry.restore(7,State(1))
        def coordinate(change,apply):self.registry.restore(7,State(2));return apply()
        self.service.coordinate=coordinate
        self.assertEqual(self.call('reset',{}).status,503);self.assertEqual(self.registry.get(7).fails,2)
    def test_bound_rejected_before_approval(self):
        self.make([]);self.service.max_rows=1
        self.registry.restore(7,State());self.registry.restore(8,State());approved=[]
        self.service.mutate=lambda *a:approved.append(a) or True
        self.assertEqual(self.call('reset',{}).status,503);self.assertEqual(approved,[])
    def test_partial_forget_failure_is_uncertain_without_retry(self):
        calls=[]
        def forget(id):
            calls.append(id)
            if id==8:raise RuntimeError('secret credential')
        self.make([],Registry(forget=forget));self.registry.restore(7,State());self.registry.restore(8,State())
        r=self.call('reset',{});self.assertEqual(r.status,503);self.assertTrue(r.json()['needs_reconciliation'])
        self.assertNotIn(b'secret',r.body);self.assertEqual(calls,[7,8])
    def test_callback_added_id_preserved_and_uncertain(self):
        calls=[]
        def forget(id):calls.append(id);self.registry.restore(99,State(3))
        self.make([],Registry(forget=forget));self.registry.restore(7,State())
        r=self.call('reset',{});self.assertEqual(r.status,503);self.assertEqual(calls,[7])
        self.assertEqual(self.registry.get(99).fails,3)
    def test_empty_inventory_success(self):
        self.make([[],[],[]]);self.assertEqual(self.call('reset',{}).status,200)

if __name__=='__main__':unittest.main()
