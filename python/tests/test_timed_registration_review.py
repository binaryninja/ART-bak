"""Independent actual post-prepare registration; synthetic positive pins only."""
import unittest
from dataclasses import replace
from artex.db.early_completion import EarlyCompletionGrant
import test_timed_lifecycle as fixture

class RegistrationReview(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ActualRegistrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def assert_retained(self):
        f=self.f;b=f.f.bundle;h=b.runner.handles()[0]
        calls=list(f.f.p.calls)
        self.assertEqual(b.runner._timed_attempts[h],'failed')
        self.assertFalse(b.host.poll().all_settled)
        self.assertFalse(b.host.drain(0).all_settled)
        with self.assertRaises(Exception):b.host.advance_owned(lambda:b.runner.advance(h.task_id,h.token,2))
        self.assertEqual(f.f.p.calls,calls)
        self.assertIsNone(h._child.timing.store.lease)
        self.assertIn(h,b.runner.handles())
        return b,h

    def test_permit_issuance_cancellation_retains_partial_permit(self):
        f=self.f;issued=[]
        def issue(r):
            grant=EarlyCompletionGrant(r);issued.append(grant)
            f.f.bundle.runner.handles()[0].token.cancel('paused_by_user')
            return grant
        with self.assertRaises(Exception):f.prepare(issue)
        b,h=self.assert_retained()
        self.assertEqual(b.timed_lifecycle._entries[h].products,issued)
        self.assertTrue(b.timed_lifecycle.failed(h))

    def test_seal_during_issue_retains_unregistered_products(self):
        f=self.f;issued=[]
        def issue(r):
            grant=EarlyCompletionGrant(r);issued.append(grant)
            f.f.bundle.manual_actions.stop_new_actions()
            return grant
        with self.assertRaises(Exception):f.prepare(issue)
        b,h=self.assert_retained()
        products=b.timed_lifecycle._entries[h].products
        self.assertIs(products[0],issued[0]);self.assertGreaterEqual(len(products),2)
        self.assertEqual(b.timed_lifecycle.status().registered,0)

    def test_foreign_permit_receipt_fails_without_observer_or_frontier(self):
        def issue(r):return EarlyCompletionGrant(replace(r,task_id=6))
        with self.assertRaises(Exception):self.f.prepare(issue)
        b,h=self.assert_retained()
        self.assertEqual(len(b.timed_lifecycle._entries[h].products),1)
        self.assertEqual(b.runner._frontier,{})

    def test_registration_callback_cannot_reenter_or_settle(self):
        f=self.f;original=f.policy.issue_frontier_grant;observed=[]
        def issue(r):
            b=f.f.bundle;h=b.runner.handles()[0]
            observed.append(b.host.poll().busy)
            observed.append(b.host.advance_owned(lambda:None).busy)
            observed.append(b.host.drain(0).all_settled)
            try:b.runner.advance(h.task_id,h.token,2)
            except Exception:observed.append('denied')
            else:observed.append('accepted')
            return original(r)
        b=f.prepare(issue)
        self.assertEqual(observed,[True,True,False,'denied'])
        self.assertEqual(f.calls,['issue'])
        h=b.runner.handles()[0]
        self.assertIsNone(h._child.timing.store.lease)
        self.assertEqual(f.f.p.calls,[1,1])
        self.assertTrue(b.timed_lifecycle.registered(h))

    def test_blocked_issuance_keeps_host_slot_and_partial_products_visible(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        f=self.f;entered=Event();release=Event();original=f.policy.issue_frontier_grant
        def issue(r):
            entered.set()
            if not release.wait(3):raise RuntimeError('inert callback timeout')
            return original(r)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(f.prepare,issue)
            try:
                self.assertTrue(entered.wait(1))
                b=f.f.bundle;h=b.runner.handles()[0]
                self.assertEqual(b.runner._timed_attempts[h],'registering')
                self.assertIn(h,b.timed_lifecycle._entries)
                self.assertTrue(b.host.poll().busy)
                self.assertFalse(b.host.drain(0).all_settled)
                self.assertIsNone(h._child.timing.store.lease)
                self.assertEqual(f.f.p.calls,[1,1])
            finally:release.set()
            b=pending.result(1)
        self.assertTrue(b.timed_lifecycle.registered(h))
        self.assertFalse(b.host.owns_work(b.runner))

    def test_policy_mutation_during_issuance_is_retained_failure(self):
        f=self.f;original=f.policy.issue_frontier_grant
        def issue(r):
            grant=original(r)
            object.__setattr__(f.policy,'observation_authorize',lambda *a:True)
            return grant
        with self.assertRaises(Exception):f.prepare(issue)
        b=f.f.bundle;h=b.runner.handles()[0]
        self.assertEqual(b.runner._timed_attempts[h],'failed')
        self.assertTrue(b.timed_lifecycle._entries[h].failed)
        self.assertEqual(len(b.timed_lifecycle._entries[h].products),1)
        calls=list(f.f.p.calls)
        with self.assertRaises(Exception):b.host.advance_owned(lambda:b.runner.advance(h.task_id,h.token,2))
        self.assertEqual(f.f.p.calls,calls)
        self.assertIsNone(h._child.timing.store.lease)
