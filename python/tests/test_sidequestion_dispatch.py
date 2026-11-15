"""Controlled inert threads with fake providers and test-only stores."""
from dataclasses import replace
from threading import Event
import unittest
from test_sidequestion_runtime import Store,Provider,SNAP
from artex.db.sidequestions import Parent,SideInvalid
from artex.sidequestion.runtime import SideQuestionCoordinator,ProviderBinding,SideUnavailable,SidePersistenceError
from artex.sidequestion.dispatch import SideQuestionDispatcher

class BlockingProvider(Provider):
    def __init__(self):super().__init__();self.entered=Event();self.release=Event()
    def complete(self,request):
        self.entered.set()
        if not self.release.wait(3):raise RuntimeError('inert test timed out')
        return super().complete(request)

class DispatchTests(unittest.TestCase):
    def setup_dispatch(self,provider=None,**kwargs):
        provider=provider or Provider()
        c=SideQuestionCoordinator(lambda m:ProviderBinding(m,provider))
        d=SideQuestionDispatcher(c,**kwargs)
        self.addCleanup(lambda:d.close(cancel=True,timeout=3))
        if isinstance(provider,BlockingProvider):self.addCleanup(provider.release.set)
        return c,d,provider

    def store(self,id):return Store(replace(SNAP,parent=Parent(conversation_id=id)))

    def test_dispatch_exact_handle_once_and_receipt(self):
        c,d,p=self.setup_dispatch();store=self.store(1)
        a=c.admit(store,'client','original question',agent_question='prepared expanded question')
        self.assertIs(d.dispatch(a),True);self.assertIs(d.dispatch(a),True)
        result=d.receipt(a.handle).result(2)
        self.assertTrue(result.persisted);self.assertEqual(store.row.question,'original question')
        self.assertIn('prepared expanded question',str(p.calls[0]));self.assertEqual(len(p.calls),1)
        self.assertIs(d.dispatch(a),True);self.assertEqual(len(p.calls),1)
        state=d.close(timeout=2)
        self.assertTrue(state.drained);self.assertEqual(state.alive_workers,0)
        self.assertEqual(state.failed_executions,0)

    def test_full_queue_durably_aborts_unstarted_and_releases_slot(self):
        c,d,p=self.setup_dispatch(BlockingProvider(),workers=1,capacity=1)
        a=c.admit(self.store(1),'a','question');d.dispatch(a)
        self.assertTrue(p.entered.wait(2))
        b=c.admit(self.store(2),'b','question');d.dispatch(b)
        third=self.store(3);rejected=c.admit(third,'c','question')
        with self.assertRaises(SideUnavailable):d.dispatch(rejected)
        self.assertEqual(third.row.status,'failed');self.assertIn('not dispatched',third.row.error)
        self.assertIsNone(d.receipt(rejected.handle))
        with self.assertRaises(SideInvalid):c.execute(rejected.handle)
        # A fresh admission is possible because the rejected slot was released.
        retry=c.admit(third,'new','question');c.abort_unstarted(retry.handle)
        p.release.set();self.assertTrue(d.receipt(a.handle).result(2).persisted)
        self.assertTrue(d.receipt(b.handle).result(2).persisted)
        self.assertEqual(len(p.calls),2)

    def test_closed_rejection_is_terminal_without_provider(self):
        c,d,p=self.setup_dispatch();d.close(timeout=2);store=self.store(1)
        a=c.admit(store,'client','question')
        with self.assertRaises(SideUnavailable):d.dispatch(a)
        self.assertEqual(store.row.status,'failed');self.assertEqual(p.calls,[])

    def test_abort_failure_retains_owner_and_can_retry(self):
        c,d,p=self.setup_dispatch();d.close(timeout=2);store=self.store(1)
        a=c.admit(store,'client','question');store.fail_writes=3
        with self.assertRaises(SidePersistenceError) as raised:d.dispatch(a)
        self.assertIs(raised.exception.handle,a.handle)
        self.assertEqual(store.row.status,'running')
        state=d.close(timeout=0);self.assertTrue(state.drained);self.assertEqual(state.unresolved_rejections,1)
        self.assertEqual(d.retry_rejections(),0);self.assertEqual(store.row.status,'failed')
        with self.assertRaises(SideInvalid):c.execute(a.handle)
        self.assertEqual(p.calls,[])

    def test_abort_does_not_steal_executing_admission(self):
        c,d,p=self.setup_dispatch(BlockingProvider());store=self.store(1)
        a=c.admit(store,'client','question');d.dispatch(a);self.assertTrue(p.entered.wait(2))
        with self.assertRaises(SideInvalid):c.abort_unstarted(a.handle)
        self.assertEqual(store.row.status,'running')
        p.release.set();self.assertTrue(d.receipt(a.handle).result(2).persisted)

    def test_close_cancel_reports_live_worker_until_provider_returns(self):
        c,d,p=self.setup_dispatch(BlockingProvider(),workers=1,capacity=1)
        s1,s2=self.store(1),self.store(2)
        a=c.admit(s1,'a','question');d.dispatch(a);self.assertTrue(p.entered.wait(2))
        b=c.admit(s2,'b','question');d.dispatch(b)
        state=d.close(cancel=True,timeout=0.01)
        self.assertFalse(state.drained);self.assertEqual(state.alive_workers,1)
        self.assertFalse(d.receipt(a.handle).done())
        p.release.set();state=d.close(timeout=2)
        self.assertTrue(state.drained)
        self.assertEqual(s1.row.status,'cancelled');self.assertEqual(s2.row.status,'cancelled')
        self.assertEqual(len(p.calls),1)
        self.assertEqual(s1.row.usage,{'input_tokens':5,'output_tokens':2})

    def test_future_cancel_does_not_orphan_queued_request(self):
        c,d,p=self.setup_dispatch(BlockingProvider(),workers=1,capacity=1)
        a=c.admit(self.store(1),'a','question');d.dispatch(a);self.assertTrue(p.entered.wait(2))
        second=self.store(2);b=c.admit(second,'b','question');d.dispatch(b)
        d.receipt(b.handle).cancel();p.release.set()
        self.assertTrue(d.close(timeout=2).drained)
        self.assertEqual(second.row.status,'completed');self.assertEqual(len(p.calls),2)

    def test_worker_persistence_failure_stops_new_dispatch_and_is_retained(self):
        c,d,p=self.setup_dispatch();store=self.store(1);a=c.admit(store,'a','question')
        store.fail_writes=10;d.dispatch(a)
        with self.assertRaises(SidePersistenceError):d.receipt(a.handle).result(2)
        self.assertIn(a.handle,d.failures())
        other=self.store(2);b=c.admit(other,'b','question')
        with self.assertRaises(SideUnavailable):d.dispatch(b)
        self.assertEqual(other.row.status,'failed')
        state=d.close(timeout=2);self.assertEqual(state.failed_executions,1);self.assertTrue(state.drained)

    def test_abort_cas_after_clear_does_not_recreate(self):
        c,d,p=self.setup_dispatch();store=self.store(1);a=c.admit(store,'client','question')
        store.clear_for_test();outcome=c.abort_unstarted(a.handle)
        self.assertTrue(outcome.stale);self.assertFalse(outcome.persisted);self.assertIsNone(store.row)

    def test_coordinator_handle_claim_prevents_two_dispatchers(self):
        c,d,p=self.setup_dispatch(BlockingProvider())
        other=SideQuestionDispatcher(c);self.addCleanup(lambda:other.close(cancel=True,timeout=3))
        store=self.store(1);a=c.admit(store,'client','question');d.dispatch(a)
        self.assertTrue(p.entered.wait(2))
        with self.assertRaises(SideInvalid):other.dispatch(a)
        with self.assertRaises(SideInvalid):c.execute(a.handle)
        self.assertEqual(store.row.status,'running')
        p.release.set();self.assertTrue(d.receipt(a.handle).result(2).persisted)
        forged=replace(a,exchange=replace(a.exchange,id='different'))
        with self.assertRaises(SideInvalid):d.dispatch(forged)
        self.assertEqual(len(p.calls),1)

    def test_invalid_configuration_and_uncreated_admission(self):
        c,d,p=self.setup_dispatch();store=self.store(1);a=c.admit(store,'client','question')
        with self.assertRaises(SideInvalid):d.dispatch(replace(a,created=False))
        c.abort_unstarted(a.handle)
        for kwargs in ({'workers':0},{'capacity':0},{'workers':5},{'capacity':True}):
            with self.assertRaises(SideInvalid):SideQuestionDispatcher(c,**kwargs)

if __name__=='__main__':unittest.main()
